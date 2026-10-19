#!/bin/bash
# move-from-host.sh <ssh-host> <remote-base> <dest-subdir> <list-file>
#
# Verified move of another laptop's folders onto smd-ops, relayed through this
# machine: files are read on <ssh-host> over SSH and streamed straight into
# smd-ops (nothing is written locally). Each entry of list-file (relative to
# remote-base, "./"-prefixed) is checked against a sha256 manifest built ON the
# source host. Verified entries are recorded on the SOURCE host's ledger
# (~/.smd-ops-moved-<host>_<sub>.txt); laptop-offboard.py deletes from the
# manifests themselves, file by file.
set -uo pipefail
host="$1"; base="$2"; sub="$3"; list="$4"
dest="/home/ops/archive/$sub"
app="${OPS_APP:-smd-ops}"  # a client's documents go to its workspace: OPS_APP=ops-<slug>
short=$(ssh -o BatchMode=yes "$host" 'hostname -s')
ledger=".${app}-moved-$(echo "$sub" | sed "s|^$short/|${short}_|" | tr '/' '_').txt"
ssh -o BatchMode=yes "$host" "touch ~/$ledger"
fly ssh console -a "$app" -u ops -C "mkdir -p $dest" > /dev/null 2>&1
ok=0; failed=0
while IFS= read -r -u 3 e; do
  [ -z "$e" ] && continue
  if ssh -o BatchMode=yes "$host" "grep -Fxq -- '$e' ~/$ledger" < /dev/null; then ok=$((ok+1)); continue; fi
  tag=$(date +%s)
  manifest=$(mktemp)
  ssh -o BatchMode=yes "$host" "cd '$base' && find '$e' -type f -print0 | xargs -0 shasum -a 256" < /dev/null > "$manifest"
  n=$(wc -l < "$manifest" | tr -d ' ')
  for try in 1 2 3; do
    ssh -o BatchMode=yes "$host" "cd '$base' && tar -cf - '$e'" < /dev/null \
      | fly ssh console -a "$app" -u ops --pty=false -C "tar -xf - -C $dest" > /dev/null 2>&1
    fly ssh console -a "$app" -u ops --pty=false -C "tee $dest/.manifest-$tag.sha256" < "$manifest" > /dev/null 2>&1
    bad=$(fly ssh console -a "$app" -u ops -C "sh -c 'cd $dest && sha256sum -c --quiet .manifest-$tag.sha256 2>&1 | wc -l'" < /dev/null 2>/dev/null | tail -1 | tr -d ' \r')
    if [ "$n" -gt 0 ] && [ "$bad" = "0" ]; then
      ssh -o BatchMode=yes "$host" "echo '$e' >> ~/$ledger" < /dev/null
      ok=$((ok+1)); echo "ok   $e ($n files)"; break
    fi
    [ "$try" = 3 ] && { failed=$((failed+1)); echo "FAIL $e (files=$n mismatches=${bad:-unknown})"; }
  done
  rm -f "$manifest"
done 3< "$list"
echo "SUMMARY $sub: verified=$ok failed=$failed"
