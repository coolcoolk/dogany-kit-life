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
  shim   .claude/skills-bundle/<old>/ becomes a plain dir of relative
         symlinks (../<new>/<entry>) to every top-level entry of the new
         skill dir EXCEPT SKILL.md, plus a .renamed-to marker. A path probe
         from outside the kit that falls back to the bundle path (pack_health
         diet-plan probes the old capture id's lookup.py under .claude/skills
         then under .claude/skills-bundle) keeps resolving; with
         no SKILL.md the shim is not a skill. Skipped when anything unmarked
         already sits at the old path; a marked shim is re-synced when the new
         dir gained or lost an entry (or still holds pre-2.46 absolute links).
         The activation path .claude/skills/<old> is NEVER shimmed: whatever
         sits there is read as a live skill, and the declared-actual watch
         (DGN-1114/1368) reports a real dir there as shadowing the bundle
         copy and an id no product ships as live_orphan.
  unshim a marked shim an earlier carry laid at .claude/skills/<old>/ (only
         symlinks + the marker) is removed; nothing with bytes is touched
         (one holding a real file is reported and left for a hand).
drop removes only shims carrying the marker, once the callers are renamed.

carry also RETIRES an agent-local skill the kit now ships under its own id
(service/kit_life/local-skill-retire.tsv: <local id> TAB <kit id> TAB
<fingerprint file>; module_dining v1: the life agent's restaurant-find -> dining-session).
The framework's retire (DGN-1422) only sees ids the kit itself shipped, so a
skill an agent grew locally would keep firing next to its kit replacement.
Retired only when .claude/skills/<local> is a REAL dir (not a link) whose
SKILL.md says `name: <local>` and which holds the fingerprint file -- an
unrelated skill of the same name is never touched. Then, in order:
  archive  the dir moves to .claude/skills-retired/<local>.<ts>/ (the
           framework's own archive place and name form); nothing is deleted
  conf     BUNDLE_<KIT ID>=on when that key is absent or empty (the feature
           was in use; an explicit off is kept)
  link     .claude/skills/<kit id> -> ../skills-bundle/<kit id> when absent
           and the bundle dir is installed, so the replacement is live in the
           same step and there is no turn without the feature.

Output: one line per action, `<would|did> <what>`; exit 0. No database.
"""
import os
import sys
import time

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


def retire_table(script_dir):
    path = os.path.join(script_dir, "..", "service", "kit_life",
                        "local-skill-retire.tsv")
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for ln in fh:
            if not ln.strip() or ln.startswith("#"):
                continue
            p = ln.rstrip("\n").split("\t")
            if len(p) >= 3 and all(p[:3]):
                rows.append((p[0], p[1], p[2]))
    return rows


def _local_skill_name(d):
    try:
        with open(os.path.join(d, "SKILL.md"), encoding="utf-8") as fh:
            for ln in fh:
                if ln.startswith("name:"):
                    return ln.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _retire_local(root, rows, apply, out):
    for old, new, finger in rows:
        src = os.path.join(root, ".claude", "skills", old)
        if (os.path.islink(src) or not os.path.isdir(src)
                or _local_skill_name(src) != old
                or not os.path.isfile(os.path.join(src, finger))
                # a member that was not delivered the kit skill keeps its own
                or not os.path.isdir(os.path.join(root, ".claude", "skills-bundle", new))):
            continue
        dst = os.path.join(root, ".claude", "skills-retired",
                           "%s.%s" % (old, time.strftime("%Y%m%d-%H%M%S")))
        out.append("%s retire .claude/skills/%s -> .claude/skills-retired/%s (replaced by %s)"
                   % ("did" if apply else "would", old, os.path.basename(dst), new))
        if apply:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.rename(src, dst)
        _enable(root, new, apply, out)


def _enable(root, new, apply, out):
    path = os.path.join(root, "config", "kit_life.conf")
    key = _key(new)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        cur = None
        for ln in lines:
            if ln.split("=", 1)[0].strip() == key and "=" in ln:
                cur = ln.split("=", 1)[1].strip()
        if not cur:
            out.append("%s conf %s=on (local skill retired)" % ("did" if apply else "would", key))
            if apply:
                if cur is None:
                    at = len(lines) - 1 if lines and lines[-1] == "" else len(lines)
                    lines.insert(at, key + "=on")
                else:
                    lines = [(key + "=on") if ln.split("=", 1)[0].strip() == key else ln
                             for ln in lines]
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write("\n".join(lines))
                os.replace(tmp, path)
    link = os.path.join(root, ".claude", "skills", new)
    if (not os.path.lexists(link)
            and os.path.isdir(os.path.join(root, ".claude", "skills-bundle", new))):
        out.append("%s link .claude/skills/%s -> ../skills-bundle/%s"
                   % ("did" if apply else "would", new, new))
        if apply:
            os.symlink(os.path.join("..", "skills-bundle", new), link)


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


def _want_links(src, new):
    return {n: os.path.join("..", new, n)
            for n in sorted(os.listdir(src)) if n != "SKILL.md"}


def _is_shim(dst):
    """A carry-made shim: a real dir holding the marker and only symlinks."""
    if os.path.islink(dst) or not os.path.isfile(os.path.join(dst, MARKER)):
        return False
    return all(n == MARKER or os.path.islink(os.path.join(dst, n))
               for n in os.listdir(dst))


def _shim(root, rows, apply, out):
    base = ".claude/skills-bundle"
    for old, new in rows:
        src = os.path.join(root, ".claude", "skills-bundle", new)
        dst = os.path.join(root, base, old)
        if not os.path.isdir(src):
            continue
        want = _want_links(src, new)
        if not os.path.lexists(dst):
            out.append("%s shim %s/%s -> %s" % (
                "did" if apply else "would", base, old, new))
            if apply:
                os.makedirs(dst)
                for name, target in want.items():
                    os.symlink(target, os.path.join(dst, name))
                with open(os.path.join(dst, MARKER), "w", encoding="utf-8") as fh:
                    fh.write(new + "\n")
            continue
        if not _is_shim(dst):
            continue  # an unmarked dir or a link: never ours to change
        have = {n: os.readlink(os.path.join(dst, n))
                for n in os.listdir(dst) if n != MARKER}
        with open(os.path.join(dst, MARKER), encoding="utf-8") as fh:
            marked = fh.read().strip()
        if have == want and marked == new:
            continue
        out.append("%s reshim %s/%s -> %s" % (
            "did" if apply else "would", base, old, new))
        if apply:
            for name in have:
                if have[name] != want.get(name):
                    os.unlink(os.path.join(dst, name))
            for name, target in want.items():
                if have.get(name) != target:
                    os.symlink(target, os.path.join(dst, name))
            with open(os.path.join(dst, MARKER), "w", encoding="utf-8") as fh:
                fh.write(new + "\n")


def _unshim_active(root, rows, apply, out):
    for old, _new in rows:
        dst = os.path.join(root, ".claude", "skills", old)
        if os.path.islink(dst) or not os.path.isfile(os.path.join(dst, MARKER)):
            continue
        if not _is_shim(dst):
            out.append("left .claude/skills/%s (marked shim holds a real file; "
                       "move it by hand)" % old)
            continue
        out.append("%s unshim .claude/skills/%s (activation path; the bundle "
                   "shim keeps path probes)" % ("did" if apply else "would", old))
        if apply:
            for n in os.listdir(dst):
                os.unlink(os.path.join(dst, n))
            os.rmdir(dst)


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
            _unshim_active(root, rows, apply, out)
            _shim(root, rows, apply, out)
            _retire_local(root, retire_table(script_dir), apply, out)
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
