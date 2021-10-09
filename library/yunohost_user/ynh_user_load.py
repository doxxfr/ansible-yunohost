# https://github.com/YunoHost/yunohost/pull/951#issue-604908363
from yunohost.user import user_create
import yunohost
import sys
sys.path.insert(0, "/usr/lib/moulinette/")
# possibly set debug=True if needed, but when writing script supposedly you ain't debugging yunohost itself, and full log is still available in yunohost-cli.log and yunohost log list...
yunohost.init_logging()
yunohost.init_i18n()


user_create(username="foo", ...)
