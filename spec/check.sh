#!/usr/bin/env bash
set -euo pipefail

SPEC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SPEC_DIR/.." && pwd)"
MODEL="RenderJob"
CACHE_DIR="${HOME}/.cache/ambitious"
MAX_BROKEN_DIFF_LINES=2
MIN_DISTINCT_STATES=1000

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

sha256_of() {
  if command -v sha256sum >/dev/null; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

pinned() {
  (cd "$REPO_DIR" && mise exec -- printenv "$1") || fail "$1 is not set in the [env] table of .mise.toml"
}

fetch_tla_jar() {
  mkdir -p "$CACHE_DIR"
  if [ -f "$TLA_JAR" ] && [ "$(sha256_of "$TLA_JAR")" = "$TLA_SHA256" ]; then
    return
  fi
  local partial actual
  partial="$(mktemp "${CACHE_DIR}/tla2tools.XXXXXX")"
  curl -sSfL -o "$partial" "$TLA_URL" || { rm -f "$partial"; fail "could not download $TLA_URL"; }
  actual="$(sha256_of "$partial")"
  if [ "$actual" != "$TLA_SHA256" ]; then
    rm -f "$partial"
    fail "tla2tools.jar SHA-256 is $actual, but .mise.toml pins $TLA_SHA256. Nothing was kept. To move to a new version on purpose, change TLA2TOOLS_URL and TLA2TOOLS_SHA256 together"
  fi
  mv -f "$partial" "$TLA_JAR"
}

run_tlc() {
  local tla="$1" cfg="$2" out="$3" meta code
  meta="$(mktemp -d)"
  set +e
  (cd "$REPO_DIR" && mise exec -- java -XX:+UseParallelGC -cp "$TLA_JAR" tlc2.TLC \
    -workers 1 -metadir "$meta" -noGenerateSpecTE -config "$cfg" "$tla") >"$out" 2>&1
  code=$?
  set -e
  rm -rf "$meta"
  return "$code"
}

state_count() {
  grep -E '^[0-9]+ states generated, [0-9]+ distinct states found' "$1" | tail -1
}

expected_entries() {
  python3 - "$SPEC_DIR/broken/EXPECTED.json" <<'PY'
import json
import re
import sys

name = re.compile(r"[A-Za-z0-9_]+")
with open(sys.argv[1], encoding="utf-8") as handle:
    expected = json.load(handle)
for variant, entry in expected.items():
    invariant = entry.get("invariant", "") if isinstance(entry, dict) else ""
    because = entry.get("because", "") if isinstance(entry, dict) else ""
    complete = (
        name.fullmatch(variant)
        and isinstance(invariant, str)
        and name.fullmatch(invariant)
        and isinstance(because, str)
        and because.strip() != ""
    )
    if not complete:
        sys.exit(f"entry {variant} needs an invariant and a because")
    print(variant, invariant)
PY
}

cfg_rules() {
  awk -v wanted="$2" '
    /^[A-Z_]+/ { section = $1; next }
    NF == 1 && section ~ wanted { print $1 }
  ' "$1"
}

check_passing() {
  local cfg="$1" name out distinct invariants properties
  name="$(basename "$cfg" .cfg)"
  ! grep -q '^CHECK_DEADLOCK' "$cfg" || fail "$name: remove CHECK_DEADLOCK from $name.cfg, a stuck state must stay an error"
  out="$(mktemp)"
  if ! run_tlc "$SPEC_DIR/$name.tla" "$cfg" "$out"; then
    cat "$out"
    fail "$name: TLC reported an error"
  fi
  grep -q 'Model checking completed. No error has been found.' "$out" || { cat "$out"; fail "$name: TLC did not finish"; }
  distinct="$(state_count "$out" | sed -E 's/.*, ([0-9]+) distinct.*/\1/')"
  [ "${distinct:-0}" -gt "$MIN_DISTINCT_STATES" ] || fail "$name: only ${distinct:-0} distinct states, the model is not exploring"
  invariants="$(cfg_rules "$cfg" '^INVARIANTS?$' | tr '\n' ' ')"
  properties="$(cfg_rules "$cfg" '^PROPERT(Y|IES)$' | tr '\n' ' ')"
  echo "PASS  $name: ${invariants:+${invariants}hold in every state; }${properties:+${properties}hold on every run; }no state is stuck ($(state_count "$out"))"
  rm -f "$out"
}

check_broken() {
  local name="$1" invariant="$2" cfg tla out changed
  cfg="$SPEC_DIR/broken/$name.cfg"
  tla="$SPEC_DIR/broken/$name.tla"
  [ -f "$cfg" ] && [ -f "$tla" ] || fail "$name: listed in EXPECTED.json but $name.tla or $name.cfg is missing"
  cmp -s "$cfg" "$SPEC_DIR/$MODEL.cfg" || fail "$name: $name.cfg must be the same as $MODEL.cfg, so the variant is checked against every rule with the same bounds"
  changed="$(diff <(sed "s/MODULE $MODEL/MODULE $name/" "$SPEC_DIR/$MODEL.tla") "$tla" | grep -c '^[<>]' || true)"
  [ "$changed" -ge 1 ] || fail "$name: identical to $MODEL.tla"
  [ "$changed" -le "$MAX_BROKEN_DIFF_LINES" ] || fail "$name: differs from $MODEL.tla in $changed lines, more than one changed step"
  out="$(mktemp)"
  if run_tlc "$tla" "$cfg" "$out"; then
    cat "$out"
    fail "$name: expected $invariant to be violated, but TLC passed"
  fi
  grep -q "^Error: Invariant ${invariant} is violated\.\$" "$out" || { cat "$out"; fail "$name: TLC failed, but not on invariant $invariant"; }
  echo "PASS  broken/$name: breaks $invariant and no other rule first ($(state_count "$out"))"
  rm -f "$out"
}

command -v mise >/dev/null || fail "mise is required (https://mise.jdx.dev)"
command -v python3 >/dev/null || fail "python3 is required to read spec/broken/EXPECTED.json"
(cd "$REPO_DIR" && mise trust --quiet && mise install --quiet)
TLA_URL="$(pinned TLA2TOOLS_URL)"
TLA_SHA256="$(pinned TLA2TOOLS_SHA256)"
[[ "$TLA_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "TLA2TOOLS_SHA256 in .mise.toml must be 64 lowercase hexadecimal characters"
TLA_JAR="${CACHE_DIR}/tla2tools-${TLA_SHA256}.jar"
fetch_tla_jar
echo "TLC from tla2tools.jar sha256 ${TLA_SHA256}"
(cd "$REPO_DIR" && mise exec -- java -version 2>&1 | head -1)

shopt -s nullglob
passing=("$SPEC_DIR"/*.cfg)
broken=("$SPEC_DIR"/broken/*.cfg)
[ "${#passing[@]}" -ge 1 ] || fail "no spec/*.cfg found"

entries="$(expected_entries)" || fail "spec/broken/EXPECTED.json must have the shape { \"<variant>\": { \"invariant\": \"<name>\", \"because\": \"<plain sentence>\" } }"
listed="$(printf '%s\n' "$entries" | grep -c . || true)"
[ "$listed" -eq "${#broken[@]}" ] || fail "EXPECTED.json lists $listed variants, spec/broken has ${#broken[@]}"

for cfg in "${passing[@]}"; do
  for rule in $(cfg_rules "$cfg" '^INVARIANTS?$'); do
    [ "$rule" = "TypeOK" ] && continue
    printf '%s\n' "$entries" | grep -Eq "^[A-Za-z0-9_]+ $rule\$" || fail "$(basename "$cfg"): no broken variant breaks $rule, so nothing shows the model can catch it"
  done
done

for cfg in "${passing[@]}"; do check_passing "$cfg"; done
while read -r name invariant; do check_broken "$name" "$invariant"; done <<<"$entries"
echo "OK: ${#passing[@]} model(s) pass, ${#broken[@]} broken variant(s) break the expected rule"
