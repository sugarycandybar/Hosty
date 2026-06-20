project('hosty',
  version: run_command('python3', '-c', 'import os; exec(open("hosty/version.py").read()); print(__version__)', check: true).stdout().strip(),
  meson_version: '>= 0.62.0',
)

python = import('python').find_installation('python3')

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
