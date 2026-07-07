# 项目代理约定

## Python 环境

- 本项目统一使用位于 `/root/vix/.venv` 的虚拟环境，基于 Python 3.12.3。
- 执行任何 `python` 或 `pip` 命令前，必须先激活虚拟环境，或直接使用 `.venv/bin/` 下的绝对路径。
- 推荐命令前缀：
  ```bash
  source /root/vix/.venv/bin/activate && ...
  ```
- 也可以直接使用：
  - `/root/vix/.venv/bin/python`
  - `/root/vix/.venv/bin/pip`

## 依赖管理

- 项目依赖声明在 `requirements.txt` 中。
- 安装或更新依赖时，必须使用 `.venv` 中的 pip：
  ```bash
  source /root/vix/.venv/bin/activate && pip install -r /root/vix/requirements.txt
  ```

## 运行脚本

- 运行项目脚本时，确保使用虚拟环境内的 Python：
  ```bash
  source /root/vix/.venv/bin/activate && python /root/vix/scripts/start.py
  ```
