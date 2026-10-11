# Contributing

## How to contribute

1. Fork the repo and create a branch.
2. Make your changes, keep them focused on one thing.
3. Run lint and type checks.
4. Open a pull request against `main`.

## Translations

Hosty uses `gettext`. Mark UI strings with `_("...")` and counts with
`ngettext("singular", "plural", n)` (both come from builtins via
`hosty/i18n.py`, no import needed).

The easiest way to contribute a translation is Weblate, which proposes the
`.po` changes as a pull request.

Manually (only needed to preview string changes locally):

```bash
meson setup build
meson compile -C build hosty-update-po  # refreshes po/hosty.pot and merges po/*.po
msginit -l <locale> -i po/hosty.pot -o po/<locale>.po  # new language only
```

Then add your locale to `po/LINGUAS` and open a PR. New languages show up in
the in-app picker automatically, no code changes needed. Only touch
`po/POTFILES` when adding or removing source files with translatable strings.
