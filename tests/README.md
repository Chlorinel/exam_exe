# 自动化测试

在仓库根目录运行全部测试：

```powershell
python -m unittest discover -s tests -t . -v
```

`-t .` 会把仓库根目录加入模块搜索路径，使测试可以直接导入根目录中的功能模块。
