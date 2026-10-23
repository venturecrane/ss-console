"""Seat-side planter for `fleet selftest --plant`. Runs ON THE MACHINE as root.

``fleet`` prepends one line, ``ARGS = {...}``, and ships the result on stdin
(see selftest_plant.plant_source). ARGS["action"] is:

  plant    create ONE real violation the self-test's `retired-skills-absent`
           check must catch: an empty directory named after the first retired
           skill in /app/retired-skills.txt, carrying a marker file so only a
           planted directory is ever removed. A retired skill genuinely on the
           volume is refused, never adopted.
  clear    remove the planted directory (only when the marker is in it).
  trigger  nothing but the trigger.

Every action ends by touching /run/smd-selftest/trigger/run-now, which asks the
self-test loop (the only writer of the results file) for a run now. The trigger
directory is root-only 0700 and this runs as root, which is what the loop
requires before it honours the file.
"""

import pathlib

ARGS = globals().get("ARGS") or {}
MARK = ".smd-selftest-plant"
TRIGGER_DIR = pathlib.Path("/run/smd-selftest/trigger")


def first_retired():
    with open("/app/retired-skills.txt", encoding="utf-8") as fh:
        names = [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
    return names[0] if names else None


def main():
    name = first_retired()
    if not name:
        return "error no retired skill name in /app/retired-skills.txt"
    target = pathlib.Path("/opt/data/skills") / name
    action = ARGS.get("action")
    if action == "plant":
        if (target.exists() or target.is_symlink()) and not (target / MARK).exists():
            return "error " + name + " is genuinely on the volume; not planting over a real finding"
        target.mkdir(exist_ok=True)
        (target / MARK).write_text("planted by fleet selftest --plant\n", encoding="utf-8")
    elif action == "clear":
        if (target / MARK).exists():
            (target / MARK).unlink()
            target.rmdir()
    elif action != "trigger":
        return "error bad action"
    if not TRIGGER_DIR.is_dir():
        return "error no trigger directory (an image without the self-test loop)"
    (TRIGGER_DIR / "run-now").touch()
    return "ok " + str(action) + " " + name


print("SMD_SELFTEST_PLANT " + main())
