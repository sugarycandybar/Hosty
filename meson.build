project('hosty',
  version: run_command('python3', '-c', 'import os; exec(open("hosty/version.py").read()); print(__version__)', check: true).stdout().strip(),
  meson_version: '>= 0.62.0',
)

python = import('python').find_installation('python3')

git_sha = run_command('git', 'rev-parse', '--short=8', 'HEAD', check: false).stdout().strip()
if git_sha == ''
  git_sha = 'unknown'
endif
if get_option('beta')
  build_channel = 'beta'
else
  build_channel = 'stable'
endif
build_info = configure_file(
  input: 'hosty/build_info.py.in',
  output: 'build_info.py',
  configuration: {'git_sha': git_sha, 'channel': build_channel},
)
install_data(build_info, install_dir: get_option('datadir') / 'hosty')

subdir('po')
subdir('bin')
subdir('packaging/linux')

# Install the hosty package directory to share/ (as /app/share/hosty)
install_subdir('hosty',
  install_dir: get_option('datadir'),
  strip_directory: false,
  exclude_files: ['meson.build']
)

# Install hosty.py to share/ (as /app/share/hosty.py)
install_data('hosty.py', install_dir: get_option('datadir'))
