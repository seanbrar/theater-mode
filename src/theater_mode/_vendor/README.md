# Vendored dependencies

## jeepney 0.9.0 (MIT, see `jeepney/LICENSE`)

Upstream: https://gitlab.com/takluyver/jeepney

Vendored so the daemon has no runtime dependency outside CPython. A checkout and a release
archive resolve this import identically.

`bin/vendor-jeepney` rebuilds this tree from a published wheel and applies both changes
below. The version it fetches is pinned in `.github/vendor/requirements.txt`, where
Dependabot proposes updates. `bin/check` fails while that pin, the vendored `__version__`,
and the heading above disagree, so a bump cannot merge until the tree has been rebuilt.

**Modules removed.** Only the blocking transport is used. Dropped: `io/asyncio.py`,
`io/trio.py`, `io/threading.py`, `bindgen.py` (a code generator referenced only in
comments), and every `tests/` directory. Removing `io/asyncio.py` is what keeps `asyncio`
out of the daemon's import graph, which is most of the startup cost this replaces.

**Imports rewritten.** `jeepney/io/blocking.py` and `jeepney/io/common.py` import the
package absolutely (`from jeepney import ...`), which resolves to an installed copy rather
than this one. Both were rewritten to relative form (`from .. import ...`). No other file
needed changing; the rest of the package already uses relative imports.
