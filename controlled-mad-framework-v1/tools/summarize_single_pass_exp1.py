# Archived single-pass I-T1 / MAD-T3 analysis; not the equal_rounds_v10 analysis.
import csv, json, os
from collections import defaultdict

RUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "runs", "full500-matrix-v2-20261024", "exports")
AID = "2290dc829b5758f9"
LABELS = ["Supported", "Refuted", "Not Enough Evidence", "Conflicting Evidence/Cherrypicking"]


def load(name):
    with open(os.path.join(RUN, f"{name}-{AID}.csv"), encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def j(s):
    return json.loads(s)


ops = ["ethos", "logos", "pathos", "conclusion_front", "conclusion_back", "support_first", "limitation_first"]

cs = load("condition_summaries")
print("=" * 100)
print("CONDITION SUMMARY (accuracy)")
print("=" * 100)
acc = {}
for r in cs:
    acc[(r["operation"], r["protocol"], r["variant"], int(r["round"]))] = (float(r["accuracy"]), r)
print(f"{'operation':<18}{'I-O-T1':>9}{'I-R-T1':>9}{'M-O-T3':>9}{'M-R-T3':>9}   {'dI=R-O':>9}{'dM=R-O':>9}{'D':>9}")
for op in ops:
    a_io = acc.get((op, "I", "O", 1)); a_ir = acc.get((op, "I", "R", 1))
    a_mo = acc.get((op, "M", "O", 3)); a_mr = acc.get((op, "M", "R", 3))
    if not all([a_io, a_ir, a_mo, a_mr]):
        continue
    v = [x[0] for x in (a_io, a_ir, a_mo, a_mr)]
    print(f"{op:<18}{v[0]:>9.4f}{v[1]:>9.4f}{v[2]:>9.4f}{v[3]:>9.4f}   {v[1]-v[0]:>+9.4f}{v[3]-v[2]:>+9.4f}{(v[3]-v[2])-(v[1]-v[0]):>+9.4f}")

import statistics as st
for label, idx in (("I-O-T1", 0), ("I-R-T1", 1), ("M-O-T3", 2), ("M-R-T3", 3)):
    vals = [acc[(op, "I" if idx < 2 else "M", "O" if idx % 2 == 0 else "R", 1 if idx < 2 else 3)][0] for op in ops]
    print(f"mean {label}: {st.mean(vals):.4f}")
print(f"mean dI(R-O): {st.mean([acc[(op,'I','R',1)][0]-acc[(op,'I','O',1)][0] for op in ops]):+.4f}")
print(f"mean dM(R-O): {st.mean([acc[(op,'M','R',3)][0]-acc[(op,'M','O',3)][0] for op in ops]):+.4f}")

print()
print("=" * 100)
print("SUMMARY EFFECT D = (M-R - M-O) - (I-R - I-O)")
print("=" * 100)
se = load("summary_effects")
by = {}
for r in se:
    by[(r["operation"], int(r["round"]))] = r
for op in ops:
    for rnd in (1, 2, 3):
        r = by.get((op, rnd))
        if not r:
            continue
        D = j(r["D"])
        vote = "".join(f"{D['vote'][l]:>+12.5f}" for l in LABELS)
        verd = "".join(f"{D['verdict'][l]:>+12.5f}" for l in LABELS)
        print(f"{op:<18}T{rnd} n={r['items']:>4}  vote: {vote}")
        print(f"{'':<18}       verd: {verd}")

print()
print("=" * 100)
print("STRATIFIED D BY INITIAL PATTERN (verdict layer)")
print("=" * 100)
stf = load("stratified_effects")
groups = defaultdict(list)
for r in stf:
    groups[(r["operation"], int(r["round"]), r["initial_pattern"])].append(r)
for op in ops:
    for rnd in (2, 3):
        rows = {k[2]: v[0] for k, v in groups.items() if k[0] == op and k[1] == rnd}
        if not rows:
            continue
        parts = []
        for pat in sorted(rows):
            D = j(rows[pat]["D"])["verdict"]
            parts.append(f"{pat}(n={rows[pat]['items']}): " + " ".join(f"{l[:6]}={D[l]:+.4f}" for l in LABELS))
        print(f"{op:<18}T{rnd}  " + " | ".join(parts))

print()
print("=" * 100)
print("CONSEQUENCE (mean per-item rate)")
print("=" * 100)
cg = load("consequence_summaries")
print(f"{'op':<18}{'prot':>5}{'rnd':>4}{'items':>7}{'or_flip':>10}{'c2w':>9}{'w2c':>9}")
for r in cg:
    print(f"{r['operation']:<18}{r['protocol']:>5}{r['round']:>4}{r['or_flipped_items']:>7}"
          f"{float(r['or_flipped_rate']):>10.4f}{float(r['correct_to_wrong_rate']):>9.4f}{float(r['wrong_to_correct_rate']):>9.4f}")

print()
print("=" * 100)
print("FINAL VERDICT DISTRIBUTION")
print("=" * 100)
for op in ops:
    for proto, rnd in (("I", 1), ("M", 3)):
        for var in ("O", "R"):
            r = acc.get((op, proto, var, rnd))
            if not r:
                continue
            vd = j(r[1]["verdict_distribution"])
            print(f"{op:<18}{proto}-T{rnd} {var}  " + " ".join(f"{l[:6]}={vd[l]:.3f}" for l in LABELS) + f"  no_maj={vd.get('no_majority', 0):.3f}")
