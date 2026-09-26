#!/bin/sh
# Submit the published release artifacts for multi-engine AV scanning, and
# print the verdicts. Written after Defender flagged
# nzbfast-1.0.8-windows-x64.zip as Trojan:Script/Wacatac.H!ml.
#
# Needs a free VirusTotal API key (vt account -> profile -> API key):
#   VT_API_KEY=xxxx ./packaging/av-scan.sh 1.0.8
#
# Hash lookup happens FIRST and costs nothing: if a sample has been seen
# before, you get the full verdict without uploading anything. Upload only
# when the hash is unknown.
#
# Note: uploading shares the sample with VirusTotal's partner engines and
# its subscribers. That is fine for these files - they are already public
# release assets - but do not point this at an unreleased build.
set -e
VER="${1:?usage: av-scan.sh <version>   e.g. av-scan.sh 1.0.8}"
: "${VT_API_KEY:?set VT_API_KEY (free key from virustotal.com profile)}"
REPO="${REPO:-nzbfast/nzbfast}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cd "$WORK"

echo "== fetching published artifacts + checksums =="
# The checksum asset is SHA256SUMS.txt (push-image.sh, SECURITY.md and the
# release notes all use that name); the glob also catches an older bare
# SHA256SUMS so this still works on past releases.
gh release download "v$VER" --repo "$REPO" \
  --pattern "nzbfast-$VER-windows-x64.zip" \
  --pattern "nzbfast-setup-$VER.exe" \
  --pattern "nzbfast-$VER-windows-x64-setup.exe" \
  --pattern "SHA256SUMS*" --clobber

SUMS=SHA256SUMS.txt
[ -f "$SUMS" ] || SUMS=SHA256SUMS
[ -f "$SUMS" ] || { echo "no SHA256SUMS(.txt) asset on v$VER" >&2; exit 1; }

echo
echo "== integrity: do the downloads match the published manifest? =="
# Both installer names: nzbfast-<ver>-windows-x64-setup.exe (b79aed7
# onwards) and the pre-rename nzbfast-setup-<ver>.exe, so old releases
# still verify. Matching neither used to yield an empty want.txt.
grep -E "windows-x64\.zip|windows-x64-setup\.exe|nzbfast-setup-$VER\.exe" "$SUMS" > want.txt \
  || { echo "no Windows assets listed in $SUMS - nothing to verify" >&2; exit 1; }
shasum -a 256 -c want.txt

# The engines flag the SCRIPT inside the zip, not the zip, so scan the
# members individually too - that is what pins the detection down.
mkdir -p inner && unzip -o -q "nzbfast-$VER-windows-x64.zip" -d inner
# The installer was renamed after 1.0.8 (nzbfast-setup-X.exe ->
# nzbfast-X-windows-x64-setup.exe) so it sorts beside the portable zip and
# actually says "windows". Accept either, so this still scans old releases.
TARGETS="nzbfast-$VER-windows-x64.zip"
n_setup=0
for c in "nzbfast-setup-$VER.exe" "nzbfast-$VER-windows-x64-setup.exe"; do
  [ -f "$c" ] && { TARGETS="$TARGETS $c"; n_setup=$((n_setup + 1)); }
done
# The soft guard above is for OLD releases that carry the other name, not
# for a release whose installer leg failed: with neither file present the
# run used to scan "zip only" and read green (empty-set census 21 Sep 2026,
# 2.7). A release with genuinely no installer can say so with
# AV_ALLOW_NO_INSTALLER=1; the default is to refuse.
if [ "$n_setup" -eq 0 ] && [ "${AV_ALLOW_NO_INSTALLER:-}" != 1 ]; then
  echo "av-scan: REFUSING - v$VER has NO installer asset (looked for nzbfast-setup-$VER.exe and nzbfast-$VER-windows-x64-setup.exe), so only the zip would be scanned. A failed installer leg reads exactly like this. Set AV_ALLOW_NO_INSTALLER=1 if this release genuinely shipped without one." >&2
  exit 1
fi
n_inner=0
for f in inner/*/*; do
  [ -f "$f" ] || continue
  TARGETS="$TARGETS $f"; n_inner=$((n_inner + 1))
done
if [ "$n_inner" -eq 0 ]; then
  echo "av-scan: REFUSING - nzbfast-$VER-windows-x64.zip extracted no member at inner/*/*, and the engines flag the members, not the zip. The layout moved or the zip is empty." >&2
  exit 1
fi
echo "av-scan: will scan the zip, $n_setup installer(s) and $n_inner zip member(s)"

vt_report() {   # $1 = sha256
  curl -s --request GET \
    --url "https://www.virustotal.com/api/v3/files/$1" \
    --header "x-apikey: $VT_API_KEY"
}
vt_upload() {   # $1 = path
  curl -s --request POST \
    --url https://www.virustotal.com/api/v3/files \
    --header "x-apikey: $VT_API_KEY" \
    --form "file=@$1"
}

echo
echo "== VirusTotal =="
for f in $TARGETS; do
  [ -f "$f" ] || continue
  H=$(shasum -a 256 "$f" | cut -d' ' -f1)
  R=$(vt_report "$H")
  if printf '%s' "$R" | grep -q '"NotFoundError"'; then
    echo "-- $(basename "$f"): not seen before, uploading…"
    vt_upload "$f" >/dev/null
    echo "   submitted; re-run in a minute for the verdict"
    continue
  fi
  printf '%s' "$R" | python3 -c '
import json,sys
d=json.load(sys.stdin)["data"]["attributes"]
s=d.get("last_analysis_stats",{})
print("-- %s" % sys.argv[1])
print("   malicious %s · suspicious %s · undetected %s · harmless %s"
      % (s.get("malicious"),s.get("suspicious"),s.get("undetected"),s.get("harmless")))
hits={k:v["result"] for k,v in d.get("last_analysis_results",{}).items()
      if v.get("category") in ("malicious","suspicious")}
for k,v in sorted(hits.items()): print("     %-22s %s" % (k,v))
' "$(basename "$f")"
done

cat <<'EOF'

== if a detection is confirmed as a false positive ==
Submit it, per vendor - a fixed build alone does not clear the old one:
  Microsoft  https://www.microsoft.com/en-us/wdsi/filesubmission
             (pick "Software developer", it is the queue that gets actioned)
  Others     the engine's own FP form; VirusTotal does not forward disputes.

Manual, no API key needed - upload by hand:
  https://www.virustotal.com/ (the Upload page)
  https://metadefender.com/            (~20 engines)
  https://virusscan.jotti.org/         (~15 engines, no account)
EOF
