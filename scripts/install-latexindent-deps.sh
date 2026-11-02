#!/usr/bin/env bash
# Perl modules that TinyTeX's latexindent needs (Debian/Ubuntu packages). Run with root: sudo scripts/install-latexindent-deps.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
apt-get update
apt-get install -y libyaml-tiny-perl libfile-homedir-perl libunicode-linebreak-perl liblog-dispatch-perl liblog-log4perl-perl
for m in YAML::Tiny File::HomeDir Unicode::GCString Log::Log4perl Log::Dispatch::File; do
  perl -M"$m" -e1 && echo "ok   $m" || { echo "MISSING $m" >&2; exit 1; }
done
echo "done. test as your user:  printf 'a\n  b\n' > /tmp/t.tex && ~/.TinyTeX/bin/x86_64-linux/latexindent /tmp/t.tex"
