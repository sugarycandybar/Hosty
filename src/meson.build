build_info = configure_file(
  input: 'hosty/build_info.py.in',
  output: 'build_info.py',
  configuration: build_info_conf,
)
install_data(build_info, install_dir: pkgdatadir / 'hosty')

# install_subdir cannot exclude nested __pycache__ directories, so copy the
# package explicitly to keep development caches out of the installed tree.
meson.add_install_script(
  python,
  files('install_package.py'),
  meson.current_source_dir() / 'hosty',
  get_option('datadir') / meson.project_name(),
)

hosty_conf = configuration_data()
hosty_conf.set('PYTHON', python.full_path())
hosty_conf.set('VERSION', meson.project_version())
hosty_conf.set('pkgdatadir', pkgdatadir)
hosty_conf.set('localedir', localedir)

configure_file(
  input: 'hosty.in',
  output: 'hosty',
  configuration: hosty_conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
)
