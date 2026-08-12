#!/usr/bin/env python3
r"""ArchivePro entry point (built into ArchivePro.exe). GUI with no args, CLI with args."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    argv = sys.argv[1:]
    if argv:
        from archivepro import __main__ as cli
        if hasattr(cli, 'main'):
            try:
                return cli.main(argv)
            except TypeError:
                sys.argv = ['archivepro', *argv]; return cli.main()
        sys.argv = ['archivepro', *argv]
        import runpy; runpy.run_module('archivepro', run_name='__main__'); return 0
    from archivepro import gui
    return gui.main() or 0


if __name__ == '__main__':
    sys.exit(main() or 0)
