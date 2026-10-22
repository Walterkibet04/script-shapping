#!/usr/bin/env python3
"""Small OpenType layout engine used for the proof text and for grading.

It reads the GSUB/GPOS tables that fontTools' feaLib writes into the font and
applies them to a string of codepoints. The semantics follow HarfBuzz for the
lookup types listed in SUPPORTED. Anything else raises EngineError.

    python3 shaper.py check FEATURES.fea            compare the worked lines
    python3 shaper.py shape FEATURES.fea U+E001 ... shape one line
"""
import io
import json
import os
import sys

from fontTools.feaLib.builder import addOpenTypeFeatures
from fontTools.ttLib import TTFont

SUPPORTED = {
    "GSUB": {1: "single", 2: "multiple", 4: "ligature", 6: "chaining context"},
    "GPOS": {1: "single", 2: "pair", 4: "mark-to-base"},
}
MAX_STEPS = 400000


class EngineError(Exception):
    pass


def compile_font(font_path, fea_path):
    font = TTFont(font_path)
    addOpenTypeFeatures(font, fea_path)
    buf = io.BytesIO()
    font.save(buf)
    buf.seek(0)
    return TTFont(buf)


def _val(v, name):
    return (getattr(v, name, 0) or 0) if v is not None else 0


def _eq(x):
    return lambda g: g == x


def _inset(s):
    return lambda g: g in s


def _incls(cd, c):
    return lambda g: cd.get(g, 0) == c


class Engine:
    def __init__(self, font, marks, trace=None):
        self.font = font
        self.cmap = font.getBestCmap()
        self.adv = {n: font["hmtx"][n][0] for n in font.getGlyphOrder()}
        self.marks = set(marks)
        self.trace = trace
        self.tabs = {}
        self.order = {}
        self.cache = {}
        for tag in ("GSUB", "GPOS"):
            if tag not in font:
                self.tabs[tag] = None
                self.order[tag] = []
                continue
            t = font[tag].table
            self.tabs[tag] = t
            used = set()
            if t.FeatureList:
                for fr in t.FeatureList.FeatureRecord:
                    used.update(fr.Feature.LookupListIndex)
            self.order[tag] = sorted(used)

    # ---- helpers -------------------------------------------------------
    def _t(self, *ev):
        if self.trace:
            self.trace(ev)

    def _step(self):
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise EngineError("too many steps, the lookups probably loop")

    def _skip(self, flag, name):
        return bool(flag & 8) and name in self.marks

    def _nxt(self, g, i, flag, step):
        j = i + step
        while 0 <= j < len(g):
            if not self._skip(flag, g[j]):
                return j
            j += step
        return None

    def _lookup(self, tag, idx):
        key = (tag, idx)
        if key in self.cache:
            return self.cache[key]
        lk = self.tabs[tag].LookupList.Lookup[idx]
        ltype = lk.LookupType
        subs = lk.SubTable
        if ltype in (7, 9):
            ltype = subs[0].ExtensionLookupType
            subs = [s.ExtSubTable for s in subs]
        if ltype not in SUPPORTED[tag]:
            raise EngineError("%s lookup %d has type %d, which this engine does not support"
                              % (tag, idx, ltype))
        flag = lk.LookupFlag
        if flag & ~8:
            raise EngineError("%s lookup %d: only lookupflag 0 and IgnoreMarks are supported (flag=%d)"
                              % (tag, idx, flag))
        if ltype == 6 and tag == "GSUB":
            subs = [self._chain_rules(s) for s in subs]
        res = (ltype, flag, subs)
        self.cache[key] = res
        return res

    def _chain_rules(self, st):
        rules = []
        f = st.Format
        if f == 1:
            for gl, rs in zip(st.Coverage.glyphs, st.ChainSubRuleSet):
                for r in (rs.ChainSubRule if rs else []):
                    rules.append(dict(first=_eq(gl), back=[_eq(x) for x in r.Backtrack],
                                      inp=[_eq(x) for x in r.Input],
                                      ahead=[_eq(x) for x in r.LookAhead],
                                      recs=[(a.SequenceIndex, a.LookupListIndex) for a in r.SubstLookupRecord]))
        elif f == 2:
            cov = set(st.Coverage.glyphs)
            bcd = st.BacktrackClassDef.classDefs
            icd = st.InputClassDef.classDefs
            lcd = st.LookAheadClassDef.classDefs
            for c, cs in enumerate(st.ChainSubClassSet):
                for r in (cs.ChainSubClassRule if cs else []):
                    first = (lambda cc: lambda g: g in cov and icd.get(g, 0) == cc)(c)
                    rules.append(dict(first=first, back=[_incls(bcd, x) for x in r.Backtrack],
                                      inp=[_incls(icd, x) for x in r.Input],
                                      ahead=[_incls(lcd, x) for x in r.LookAhead],
                                      recs=[(a.SequenceIndex, a.LookupListIndex) for a in r.SubstLookupRecord]))
        elif f == 3:
            sets = lambda lst: [_inset(set(c.glyphs)) for c in lst]
            inp = sets(st.InputCoverage)
            rules.append(dict(first=inp[0], back=sets(st.BacktrackCoverage), inp=inp[1:],
                              ahead=sets(st.LookAheadCoverage),
                              recs=[(a.SequenceIndex, a.LookupListIndex) for a in st.SubstLookupRecord]))
        return rules

    # ---- GSUB ----------------------------------------------------------
    def _sub_at(self, idx, g, i, nested=False):
        """Apply GSUB lookup idx at position i. Returns (applied, next_i)."""
        self._step()
        ltype, flag, subs = self._lookup("GSUB", idx)
        for st in subs:
            if ltype == 1:
                if g[i] in st.mapping:
                    new = st.mapping[g[i]]
                    self._t(idx, "s1", g[i], new)
                    g[i] = new
                    return True, i + 1
            elif ltype == 2:
                if g[i] in st.mapping:
                    seq = list(st.mapping[g[i]])
                    self._t(idx, "s2", g[i], tuple(seq))
                    g[i:i + 1] = seq
                    return True, i + len(seq)
            elif ltype == 4:
                for lig in st.ligatures.get(g[i], []):
                    pos = [i]
                    ok = True
                    j = i
                    for comp in lig.Component:
                        j = self._nxt(g, j, flag, 1)
                        if j is None or g[j] != comp:
                            ok = False
                            break
                        pos.append(j)
                    if ok:
                        self._t(idx, "s4", tuple(g[p] for p in pos), lig.LigGlyph)
                        keep = [g[k] for k in range(i + 1, pos[-1]) if k not in pos]
                        g[i:pos[-1] + 1] = [lig.LigGlyph] + keep
                        return True, i + 1
            elif ltype == 6:
                for n, r in enumerate(st):
                    ipos = self._match(g, i, flag, r)
                    if ipos is None:
                        continue
                    self._t(idx, "s6", n, tuple(g[p] for p in ipos))
                    before = len(g)
                    kinds = []
                    for seq, li in r["recs"]:
                        lt = self._lookup("GSUB", li)[0]
                        if lt == 6:
                            raise EngineError("a contextual rule may not call another contextual lookup")
                        kinds.append(lt)
                    if len(kinds) > 1 and any(k != 1 for k in kinds):
                        raise EngineError("a rule that calls a multiple or ligature lookup may only call that one lookup")
                    for seq, li in r["recs"]:
                        if seq >= len(ipos):
                            raise EngineError("bad sequence index in contextual rule")
                        self._sub_at(li, g, ipos[seq], nested=True)
                    return True, ipos[-1] + 1 + (len(g) - before)
        return False, i + 1

    def _match(self, g, i, flag, r):
        if not r["first"](g[i]):
            return None
        ipos = [i]
        j = i
        for m in r["inp"]:
            j = self._nxt(g, j, flag, 1)
            if j is None or not m(g[j]):
                return None
            ipos.append(j)
        k = ipos[-1]
        for m in r["ahead"]:
            k = self._nxt(g, k, flag, 1)
            if k is None or not m(g[k]):
                return None
        k = i
        for m in r["back"]:
            k = self._nxt(g, k, flag, -1)
            if k is None or not m(g[k]):
                return None
        return ipos

    def run_gsub(self, g):
        for idx in self.order["GSUB"]:
            ltype, flag, subs = self._lookup("GSUB", idx)
            i = 0
            while i < len(g):
                if self._skip(flag, g[i]):
                    i += 1
                    continue
                ok, i = self._sub_at(idx, g, i)

    # ---- GPOS ----------------------------------------------------------
    def run_gpos(self, g):
        n = len(g)
        self.P = [[0, 0, self.adv[x]] for x in g]  # xo, yo, xa
        self.att = {}
        for idx in self.order["GPOS"]:
            ltype, flag, subs = self._lookup("GPOS", idx)
            i = 0
            while i < len(g):
                if self._skip(flag, g[i]):
                    i += 1
                    continue
                self._step()
                done = False
                nxt = i + 1
                for st in subs:
                    r = self._pos_one(idx, ltype, flag, st, g, i)
                    if r is not None:
                        nxt = r
                        done = True
                        break
                i = nxt

    def _addv(self, k, v):
        self.P[k][0] += _val(v, "XPlacement")
        self.P[k][1] += _val(v, "YPlacement")
        self.P[k][2] += _val(v, "XAdvance")

    def _pos_one(self, idx, ltype, flag, st, g, i):
        gi = g[i]
        if ltype == 1:
            cov = st.Coverage.glyphs
            if gi not in cov:
                return None
            v = st.Value if st.Format == 1 else st.Value[cov.index(gi)]
            self._t(idx, "p1", gi, (_val(v, "XPlacement"), _val(v, "YPlacement"), _val(v, "XAdvance")))
            self._addv(i, v)
            return i + 1
        if ltype == 2:
            cov = st.Coverage.glyphs
            if gi not in cov:
                return None
            j = self._nxt(g, i, flag, 1)
            if j is None:
                return None
            gj = g[j]
            if st.Format == 1:
                ps = st.PairSet[cov.index(gi)]
                for rec in ps.PairValueRecord:
                    if rec.SecondGlyph == gj:
                        v1, v2 = rec.Value1, rec.Value2
                        break
                else:
                    return None
            else:
                c1 = st.ClassDef1.classDefs.get(gi, 0)
                c2 = st.ClassDef2.classDefs.get(gj, 0)
                if c1 >= len(st.Class1Record) or c2 >= len(st.Class1Record[c1].Class2Record):
                    return None
                rec = st.Class1Record[c1].Class2Record[c2]
                v1, v2 = rec.Value1, rec.Value2
            t1 = (_val(v1, "XPlacement"), _val(v1, "YPlacement"), _val(v1, "XAdvance"))
            t2 = (_val(v2, "XPlacement"), _val(v2, "YPlacement"), _val(v2, "XAdvance"))
            self._t(idx, "p2", gi, gj, t1, t2)
            self._addv(i, v1)
            self._addv(j, v2)
            return j + 1 if v2 is not None else j
        if ltype == 4:
            if gi not in st.MarkCoverage.glyphs:
                return None
            b = i - 1
            while b >= 0 and g[b] in self.marks:
                b -= 1
            if b < 0 or g[b] not in st.BaseCoverage.glyphs:
                return None
            mrec = st.MarkArray.MarkRecord[st.MarkCoverage.glyphs.index(gi)]
            brec = st.BaseArray.BaseRecord[st.BaseCoverage.glyphs.index(g[b])]
            ba = brec.BaseAnchor[mrec.Class]
            if ba is None:
                return None
            ma = mrec.MarkAnchor
            self._t(idx, "p4", gi)
            self.P[i][0] = ba.XCoordinate - ma.XCoordinate
            self.P[i][1] = ba.YCoordinate - ma.YCoordinate
            self.att[i] = b
            return i + 1
        return None

    def finish(self, g):
        P = [list(p) for p in self.P]
        for i in sorted(self.att):
            b = self.att[i]
            P[i][0] += self.P[b][0] - sum(self.P[k][2] for k in range(b, i))
            P[i][1] += self.P[b][1]
        return [(g[i], P[i][0], P[i][1], P[i][2]) for i in range(len(g))]

    # ---- public --------------------------------------------------------
    def shape(self, cps):
        self.steps = 0
        g = []
        for c in cps:
            if c not in self.cmap:
                raise EngineError("U+%04X is not in the font" % c)
            g.append(self.cmap[c])
        self.run_gsub(g)
        self.run_gpos(g)
        return self.finish(g)


def load_marks(path):
    return json.load(open(path))["marks"]


def parse_cp(s):
    return int(s.replace("U+", "").replace("u+", ""), 16)


def _fmt(out):
    return " ".join("%s(%d,%d,%d)" % t for t in out)


def main(argv):
    root = os.environ.get("TASK_ROOT", "/app")
    font_path = os.path.join(root, "font.ttf")
    classes = os.path.join(root, "classes.json")
    if len(argv) < 3 or argv[1] not in ("check", "shape"):
        print(__doc__)
        return 2
    font = compile_font(font_path, argv[2])
    eng = Engine(font, load_marks(classes))
    if argv[1] == "shape":
        out = eng.shape([parse_cp(a) for a in argv[3:]])
        for t in out:
            print("%s  xo=%d yo=%d adv=%d" % t)
        return 0
    proofs = json.load(open(os.path.join(root, "proofs.json")))
    good = 0
    for n, p in enumerate(proofs, 1):
        want = [tuple(x) for x in p["output"]]
        try:
            got = eng.shape([parse_cp(a) for a in p["input"]])
        except EngineError as e:
            got = None
            print("line %d: engine error: %s" % (n, e))
        if got == want:
            good += 1
        elif got is not None:
            print("line %d differs" % n)
            print("  input: " + " ".join(p["input"]))
            print("  want : " + _fmt(want))
            print("  got  : " + _fmt(got))
    print("%d/%d worked lines reproduced" % (good, len(proofs)))
    return 0 if good == len(proofs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
