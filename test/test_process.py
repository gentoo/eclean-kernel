# (c) 2020 Michał Górny <mgorny@gentoo.org>
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import tempfile
import typing
import unittest

from pathlib import Path
from unittest.mock import patch, MagicMock

from ecleankernel.bootloader import Bootloader
from ecleankernel.file import KernelFileType, GenericFile, KernelImage
from ecleankernel.kernel import Kernel
from ecleankernel.process import (
    RemovableKernelFiles,
    get_removable_files,
    get_removal_list,
    remove_stray,
    )
from ecleankernel.sort import VersionSort

from test.test_file import write_bzImage


class KernelRemovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kernels = [
            Kernel('1.old'),
            Kernel('2.new'),
            Kernel('3.stray'),
            Kernel('4.stray-files'),
        ]

        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        Path(td / 'symlink').symlink_to(td / 'kernel.old')
        write_bzImage(td / 'kernel.old', b'old built on test')
        write_bzImage(td / 'kernel.new', b'new built on test')
        with open(td / 'config-stray', 'w'):
            pass
        with open(td / 'initrd-stray.img', 'w'):
            pass
        os.mkdir(td / 'build')

        build = GenericFile(td / 'build', KernelFileType.BUILD)
        self.kernels[0].all_files = [
            KernelImage(td / 'kernel.old'),
            build,
        ]
        self.kernels[1].all_files = [
            KernelImage(td / 'kernel.new'),
            build,
        ]
        self.kernels[2].all_files = [
            build,
        ]
        self.kernels[3].all_files = [
            GenericFile(td / 'config-stray', KernelFileType.CONFIG),
            GenericFile(td / 'initrd-stray.img',
                        KernelFileType.INITRAMFS),
        ]

    def tearDown(self) -> None:
        self.td.cleanup()

    def test_remove_stray(self) -> None:
        self.assertEqual(
            list(remove_stray(self.kernels)),
            self.kernels[2:])

    def test_get_removable_files(self) -> None:
        td = Path(self.td.name)
        vmlinuz0 = td / 'kernel.old'
        config3 = td / 'config-stray'
        initrd3 = td / 'initrd-stray.img'
        self.assertEqual(
            list(get_removable_files({self.kernels[0]: ['old'],
                                      self.kernels[2]: ['no vmlinuz'],
                                      self.kernels[3]: ['no vmlinuz'],
                                      }, self.kernels)),
            [RemovableKernelFiles(self.kernels[0],
                                  ['old'],
                                  [vmlinuz0]),
             RemovableKernelFiles(self.kernels[2],
                                  ['no vmlinuz'],
                                  []),
             RemovableKernelFiles(self.kernels[3],
                                  ['no vmlinuz'],
                                  [config3, initrd3]),
             ])

    def test_removal_no_limit(self) -> None:
        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=0),
            {self.kernels[2]: ['vmlinuz does not exist'],
             self.kernels[3]: ['vmlinuz does not exist'],
             })

    def test_removal_destructive(self) -> None:
        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=1,
                             destructive=True),
            {self.kernels[2]: ['vmlinuz does not exist'],
             self.kernels[3]: ['vmlinuz does not exist'],
             self.kernels[0]: ['unwanted'],
             })

    def test_removal_no_bootloader(self) -> None:
        with self.assertRaises(SystemError):
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=1,
                             destructive=False)

    def test_version_selection(self) -> None:
        cases = [
            (['1.old'], [], [0]),
            (['1.old', '2.new'], [], [0, 1]),
            (['3.stray'], [], [2]),
            (['missing'], [], []),
            ([], [], []),
            (['1.old', '2.new', '3.stray'], ['1.old', '3.stray'], [1]),
            (None, ['1.old', '3.stray'], [1, 3]),
            (None, [k.version for k in self.kernels], []),
        ]
        for versions, excluded, expected in cases:
            with self.subTest(versions=versions, excluded=excluded):
                removals = get_removal_list(
                    self.kernels, sorter=VersionSort(), limit=None,
                    destructive=True, versions=versions,
                    excluded_versions=excluded)
                self.assertEqual(set(removals),
                                 {self.kernels[i] for i in expected})

    def test_selected_version_overrides_limit(self) -> None:
        for limit in (0, 1, 2):
            with self.subTest(limit=limit):
                self.assertEqual(
                    set(get_removal_list(
                        self.kernels, sorter=VersionSort(), limit=limit,
                        destructive=True, versions=['2.new'])),
                    {self.kernels[1]})

    def test_excluded_versions_count_toward_limit(self) -> None:
        self.assertEqual(
            set(get_removal_list(
                self.kernels, sorter=VersionSort(), limit=1,
                destructive=True, excluded_versions=['2.new', '3.stray'])),
            {self.kernels[0], self.kernels[3]})

    def test_selected_version_bootloader_protection(self) -> None:
        bootloader = MagicMock(spec=Bootloader)
        bootloader.name = 'mock'
        bootloader.return_value = [str(Path(self.td.name) / 'kernel.old')]
        self.assertEqual(
            set(get_removal_list(
                self.kernels, sorter=VersionSort(), bootloader=bootloader,
                versions=['1.old', '2.new'])),
            {self.kernels[1]})

    @patch('ecleankernel.process.os.uname')
    def test_selected_version_current(self, uname: MagicMock) -> None:
        uname.return_value = ('Linux', 'localhost', '1.old', '', 'x86_64')
        self.assertEqual(
            get_removal_list(
                self.kernels, sorter=VersionSort(), destructive=True,
                versions=['1.old']),
            {})

    def test_version_selection_no_vmlinuz(self) -> None:
        with self.assertRaises(SystemError):
            get_removal_list(
                self.kernels[2:], sorter=VersionSort(), destructive=True,
                versions=['3.stray'])

    def test_removal_bootloader_all_kernels(self) -> None:
        td = Path(self.td.name)

        class MockBootloader(Bootloader):
            name = "mock"

            def __call__(self) -> typing.Iterable[str]:
                yield str(td / "symlink")
                yield str(td / "nonexist")

        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=None,
                             destructive=False,
                             bootloader=MockBootloader()),
            {self.kernels[2]: ["vmlinuz does not exist",
                               "not referenced by bootloader (mock)"],
             self.kernels[3]: ["vmlinuz does not exist",
                               "not referenced by bootloader (mock)"],
             self.kernels[1]: ["not referenced by bootloader (mock)"],
             })

    @unittest.expectedFailure
    def test_removal_bootloader(self) -> None:
        td = Path(self.td.name)

        class MockBootloader(Bootloader):
            name = 'mock'

            def __call__(self) -> typing.Iterable[str]:
                yield str(td / 'does-not-exist')
                yield str(td / 'kernel.old')

        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=1,
                             destructive=False,
                             bootloader=MockBootloader()),
            {self.kernels[2]: ['vmlinuz does not exist',
                               'not referenced by bootloader (mock)'],
             self.kernels[3]: ['vmlinuz does not exist',
                               'not referenced by bootloader (mock)'],
             })

    @patch('ecleankernel.process.os.uname')
    def test_removal_current(self,
                             uname: MagicMock
                             ) -> None:
        uname.return_value = (
            'Linux', 'localhost', '1.old', '', 'x86_64')
        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=1,
                             destructive=True),
            {self.kernels[2]: ['vmlinuz does not exist'],
             self.kernels[3]: ['vmlinuz does not exist'],
             })

    @patch('ecleankernel.process.os.uname')
    def test_removal_current_stray(self,
                                   uname: MagicMock
                                   ) -> None:
        uname.return_value = (
            'Linux', 'localhost', '3.stray', '', 'x86_64')
        self.assertEqual(
            get_removal_list(self.kernels,
                             sorter=VersionSort(),
                             limit=1,
                             destructive=True),
            {self.kernels[3]: ['vmlinuz does not exist'],
             self.kernels[0]: ['unwanted'],
             })
