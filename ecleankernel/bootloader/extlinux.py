# (c) 2024 Boris Staletic <boris.staletic@protonmail.com>
# SPDX-License-Identifier: GPL-2.0-or-later

from ecleankernel.bootloader.lilo import LILO


class EXTLINUX(LILO):
    name = "extlinux"
    kernel_re = r"^\s*(:?LINUX|KERNEL)\s+(?P<path>.+)\s*$"
    def_path = ("/boot/extlinux/extlinux.conf",
                "/boot/syslinux/syslinux.cfg")
