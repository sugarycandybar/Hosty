desktop_file = i18n.merge_file(
  input: 'io.github.sugarycandybar.Hosty.desktop.in',
  output: 'io.github.sugarycandybar.Hosty.desktop',
  type: 'desktop',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'applications',
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

metainfo_file = i18n.merge_file(
  input: 'io.github.sugarycandybar.Hosty.metainfo.xml.in',
  output: 'io.github.sugarycandybar.Hosty.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: get_option('datadir') / 'metainfo',
)

appstreamcli = find_program('appstreamcli', required: false, disabler: true)
test('Validate appstream file', appstreamcli,
  args: ['validate', '--no-net', '--explain', metainfo_file],
)

install_data('style.css', install_dir: pkgdatadir)

install_data(
  'daemon/app.js',
  'daemon/index.html',
  'daemon/style.css',
  install_dir: pkgdatadir / 'daemon',
)

subdir('icons')
