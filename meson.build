project('hosty',
  version: run_command(
    'python3',
    '-c',
    'exec(open("src/hosty/version.py").read()); print(__version__)',
    check: true,
  ).stdout().strip(),
  meson_version: '>= 1.1.0',
)

gnome = import('gnome')
i18n = import('i18n')
python = import('python').find_installation('python3')

pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
localedir = get_option('prefix') / get_option('localedir')

git_sha = run_command('git', 'rev-parse', '--short=8', 'HEAD', check: false).stdout().strip()
if git_sha == ''
  git_sha = 'unknown'
endif
if get_option('beta')
  build_channel = 'beta'
else
  build_channel = 'stable'
endif

build_info_conf = configuration_data()
build_info_conf.set('git_sha', git_sha)
build_info_conf.set('channel', build_channel)

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
  gtk_update_icon_cache: true,
  update_desktop_database: true,
)
