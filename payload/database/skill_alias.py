"""Kit skill alias layer (2.38.0 slot rename).

The framework has no rename for a kit skill: an install retires the old id
(DGN-1422 archives .claude/skills-bundle/<old>, unlinks .claude/skills/<old>)
and links the new one (DGN-1409). What that leaves behind on an installed
instance is closed here, from the kit side, with one table:
service/kit_life/skill-aliases.tsv (<old> TAB <new>).

    kit_life.sh skill-alias list              old<TAB>new, table order
    kit_life.sh skill-alias resolve <id>      the live id for <id> (an old id
                                              maps to its new id; anything
                                              else is printed unchanged)
    kit_life.sh skill-alias carry [--apply]   after an update; dry run unless
                                              --apply
    kit_life.sh skill-alias drop [--apply]    removes what carry laid down

carry does two things, both idempotent and both never overwriting:
  conf   config/kit_life.conf: BUNDLE_<OLD>=<v> (v nonempty) is copied to
         BUNDLE_<NEW> when that key is absent or empty. The install's conf
         merge is add-only, so without this the new key stays empty and a
         later lifekit-setup reconcile reads the skill as undecided.
  shim   .claude/skills/<old>/ and .claude/skills-bundle/<old>/ become a
         plain dir of symlinks to every top-level entry of the new skill dir
         EXCEPT SKILL.md, plus a .renamed-to marker. Path probes from outside
         the kit (a pack probing `.claude/skills/<old>/lookup.py`, an
         instance routine calling `<old>/route.sh`) keep resolving; with no
         SKILL.md the shim is not a skill, so nothing activates twice.
         Skipped when anything already sits at the old path.
drop removes only shims carrying the marker, once the callers are renamed.

Output: one line per action, `<would|did> <what>`; exit 0. No database.
"""
import os
import sys

MARKER = ".renamed-to"


def table(script_dir):
    path = os.path.join(script_dir, "..", "service", "kit_life",
                        "skill-aliases.tsv")
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip() or ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 2 and p[0] and p[1]:
                rows.append((p[0], p[1]))
    return rows


def resolve(rows, sid):
    return dict(rows).get(sid, sid)


def _key(sid):
    return "BUNDLE_" + sid.replace("-", "_").upper()


def _carry_conf(root, rows, apply, out):
    path = os.path.join(root, "config", "kit_life.conf")
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    vals = {}
    for ln in lines:
        if "=" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split("=", 1)
            vals[k.strip()] = v.strip()
    changed = False
    for old, new in rows:
        v = vals.get(_key(old), "")
        if not v or vals.get(_key(new), ""):
            continue
        out.append("%s conf %s=%s (from %s)" % (
            "did" if apply else "would", _key(new), v, _key(old)))
        if _key(new) in vals:
            lines = [(_key(new) + "=" + v) if ln.split("=", 1)[0].strip() == _key(new)
                     else ln for ln in lines]
        else:
            at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
            lines.insert(at, _key(new) + "=" + v)
        vals[_key(new)] = v
        changed = True
    if changed and apply:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
        os.replace(tmp, path)


def _shim(root, rows, apply, out):
    for base in (".claude/skills", ".claude/skills-bundle"):
        for old, new in rows:
            src = os.path.join(root, ".claude", "skills-bundle", new)
            dst = os.path.join(root, base, old)
            if not os.path.isdir(src) or os.path.lexists(dst):
                continue
            out.append("%s shim %s/%s -> %s" % (
                "did" if apply else "would", base, old, new))
            if not apply:
                continue
            os.makedirs(dst)
            for name in sorted(os.listdir(src)):
                if name != "SKILL.md":
                    os.symlink(os.path.join(src, name), os.path.join(dst, name))
            with open(os.path.join(dst, MARKER), "w", encoding="utf-8") as fh:
                fh.write(new + "\n")


def _drop(root, rows, apply, out):
    for base in (".claude/skills", ".claude/skills-bundle"):
        for old, _new in rows:
            dst = os.path.join(root, base, old)
            if os.path.islink(dst) or not os.path.isfile(os.path.join(dst, MARKER)):
                continue
            names = os.listdir(dst)
            if any(n != MARKER and not os.path.islink(os.path.join(dst, n))
                   for n in names):
                out.append("kept %s/%s (holds a real file)" % (base, old))
                continue
            out.append("%s drop %s/%s" % ("did" if apply else "would", base, old))
            if apply:
                for n in names:
                    os.unlink(os.path.join(dst, n))
                os.rmdir(dst)


def cli(script_dir, argv):
    rows = table(script_dir)
    root = os.path.normpath(os.path.join(script_dir, ".."))
    if "--root" in argv:
        i = argv.index("--root")
        root = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    apply = "--apply" in argv
    argv = [a for a in argv if a != "--apply"]
    cmd = argv[0] if argv else ""
    if cmd == "list":
        for old, new in rows:
            print("%s\t%s" % (old, new))
        return 0
    if cmd == "resolve" and len(argv) == 2:
        print(resolve(rows, argv[1]))
        return 0
    if cmd in ("carry", "drop"):
        out = []
        if cmd == "carry":
            _carry_conf(root, rows, apply, out)
            _shim(root, rows, apply, out)
        else:
            _drop(root, rows, apply, out)
        for ln in out:
            print(ln)
        if not out:
            print("nothing to %s" % cmd)
        return 0
    print("usage: kit_life.sh skill-alias list | resolve <id> | "
          "carry [--apply] | drop [--apply]", file=sys.stderr)
    return 2
