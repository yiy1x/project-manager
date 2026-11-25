# 一份源码工作区，数据仍留在原处

## 维护规则

后续开发、测试与提交只从关联 `https://github.com/yiy1x/project-manager.git` 的同一 Git 工作区执行。不要在私人数据目录和一次性发布副本中分别修改代码，再整目录互相复制。

旧副本可保留作为历史快照，不必删除或重写历史；但启动入口应指向当前源码。团队或个人并行维护时，先获取远端最新状态，检查差异，再做普通快进推送或通过 PR 合入，不强推。

## 已有数据不需要搬迁

```sh
cd /path/to/project-manager
python3 app.py serve --data-dir /path/to/existing-private-data --port 8766
```

`--data-dir` 直接复用旧目录中的 SQLite、跟进范围、人工分析、提醒和时间线。静态页面、Python模块和模型输出 schema 从源码目录读取。不会把旧目录里的 Python/JS 文件当成当前版本，也不会将私人数据导入 Git。

第一次接用旧目录会在该目录生成 `instance.json` 模式标记，既有分析文件用“仅缺失时创建”的方式初始化，不重写已有内容。SQLite正常启动仍会建立缺失表和更新运行状态，这不等于搬迁或清空资料。切换前应做SQLite一致性备份，保留用户队列和自动分析开关；切换后核对原目录和数据库文件身份、提醒、手动节点及任务数量。

不要同时从新旧代码启动两个分析器处理同一份数据。停止旧服务、确认端口与worker锁释放后，再从新工作区启动。端口已占用时不要盲目杀进程，应核对监听者身份；没有开机自启。

没有私人数据的新用户可先运行 `python3 app.py serve --demo`。演示只使用虚构数据，不应指向已有私人目录。

## 获取与提交

```sh
git status --short
git remote -v
git fetch origin
git log --oneline --left-right HEAD...origin/main
```

如果工作树干净且没有本地待发布提交，使用 `git pull --ff-only` 更新。双方都有提交时先审查差异再合并，不默认丢弃任意一侧。尤其保留 Python 3.9 对应 `ubuntu-22.04` 的环境修正。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/*.test.js
git diff --check
git add -- <本次明确要提交的源码或测试文件>
python3 check_source_tree.py
git diff --cached --stat
git diff --cached
git commit -m "Describe the scoped source change"
git push origin main
```

占位的文件名要替换，不要把包含尖括号的示例原样执行。推送可能需要 GitHub CLI / Git 登录；不得将密码、访问令牌或浏览器会话内容写入仓库。

可选择启用仓库自带的本机提交前检查：

```sh
git config --local core.hooksPath .githooks
```

这只配置当前克隆，不自动改变全局 Git 设置。`check_source_tree.py` 检查的是 **Git 索引中将要提交的文件**，拒绝已知运行数据目录、分析结果、凭据文件、数据库、日志、符号链接，以及部分明显敏感文本。未跟踪数据不会被读入检查。它不承诺识别所有私密聊天、图片内容或所有种类的密钥，仍须人工核对；不要用绕过hook替代隐私确认。

## 发布后验收

- 本地 `HEAD` 与 `origin/main` 对应预期提交，远端文件没有混入运行数据。
- GitHub Actions 最终提交的 Python 3.9 / 3.13 与 Node 检查都通过。
- 本地服务使用当前主工作区代码，`--data-dir` 仍指向原私人位置。
- 运行中排序和恢复跟进正常，用户队列与提醒没有因切换入口改变。
- 只有源码同步，不把 GitHub Pages 当作 Python 后台，也不暴露私人看板。
