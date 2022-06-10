from yz.command.Command import Manager
from yz.command.py import py
from yz.command.reboot import reboot
from yz.command.shutdown import shutdown
from yz.command.op import op,deop
from yz.command.link import link


Manager.register('py',py)
Manager.register('reboot',reboot)
Manager.register('shutdown',shutdown)


Manager.register('op',op)
Manager.register('deop',deop)

Manager.register('link',link)