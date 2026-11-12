#!/bin/sh
# Build the Doltgres that projects run on: upstream Doltgres plus the fixes that are not in a
# release yet (see docs/DOLTGRES.md). Each fix is a commit on a brimleylabs fork branch with an
# open pull request upstream; when one is merged and released, drop it from the list below, and
# when the list is empty, use a stock Doltgres release instead of this script.
#
# Usage: scripts/build-doltgres.sh [output path]   (default ./doltgres)
# Needs Go (the version in doltgresql's go.mod) and ICU headers: on macOS, `brew install icu4c`
# and the CGO flags below.
set -eu

OUT=$(cd "$(dirname "${1:-./doltgres}")" && pwd)/$(basename "${1:-./doltgres}")
WORK=${WORK:-$(mktemp -d)}

# Upstream commits the fixes were tested on.
DOLTGRESQL_BASE=8ffbdff0d3
DOLT_BASE=0fec1f0fc3
GMS_BASE=9e232d4f22

# repository   fork branch                     upstream pull request
FIXES="
doltgresql      aggregate-filter               dolthub/doltgresql#3836
doltgresql      string-agg-length              dolthub/doltgresql#3837
dolt            partial-index-null-predicate   dolthub/dolt#12465
go-mysql-server join-stats-null-bound          dolthub/go-mysql-server#4150
"

checkout() {  # repository, base commit
  if [ ! -d "$WORK/$1" ]; then
    git clone -q --filter=blob:none "https://github.com/dolthub/$1.git" "$WORK/$1"
  fi
  git -C "$WORK/$1" checkout -q --detach "$2"
}

apply_fixes() {  # repository
  echo "$FIXES" | while read -r repo branch pr; do
    [ "$repo" = "$1" ] || continue
    echo "  $repo: $branch ($pr)"
    git -C "$WORK/$repo" fetch -q "https://github.com/brimleylabs/$repo.git" "$branch"
    git -C "$WORK/$repo" -c user.name=build -c user.email=build@localhost cherry-pick FETCH_HEAD >/dev/null
  done
}

echo "building in $WORK"
checkout doltgresql "$DOLTGRESQL_BASE"
checkout dolt "$DOLT_BASE"
checkout go-mysql-server "$GMS_BASE"
for repo in doltgresql dolt go-mysql-server; do apply_fixes "$repo"; done

if [ "$(uname)" = Darwin ]; then
  ICU=$(brew --prefix icu4c 2>/dev/null || true)
  [ -n "$ICU" ] && export CGO_CPPFLAGS="-I$ICU/include" CGO_LDFLAGS="-L$ICU/lib"
fi

cd "$WORK/doltgresql"
go mod edit \
  -replace "github.com/dolthub/dolt/go=$WORK/dolt/go" \
  -replace "github.com/dolthub/go-mysql-server=$WORK/go-mysql-server"
go build -o "$OUT" ./cmd/doltgres
echo "built $OUT"
"$OUT" --version
