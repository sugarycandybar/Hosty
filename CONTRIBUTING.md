# Contributing

## How to contribute

1. Fork the repo and create a branch.
2. Make your changes, keep them focused on one thing.
3. Run lint and type checks.
4. Open a pull request against `main`.

## Translations

Hosty uses Python's standard `gettext` for i18n. Strings are marked with `_("...")` in the source.

### Adding or updating a translation

1. Generate/update the `.pot` template:
   ```bash
   xgettext --from-code=UTF-8 --language=Python --keyword=_ \
     --output=po/hosty.pot --package-name=hosty \
     $(cat po/POTFILES)
   ```
2. Create or update your `.po` file:
   ```bash
   msginit -l <locale> -i po/hosty.pot -o po/<locale>.po   # new
   msgmerge -U po/<locale>.po po/hosty.pot                  # update
   ```
3. Translate the strings in the `.po` file with the tool of your choice.
4. Add your locale to `po/LINGUAS` (one code per line).
5. Submit the `.po` file in your PR.

Don't edit `po/POTFILES` unless you're adding or removing source files with translatable strings.
