"""Docker-based Spark transport for spark-submit."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class SparkJobResult:
    job_id: str
    status: str
    output: str = ""


class SparkDockerClient:
    """通过 docker exec 在 spark-master 容器内执行 spark-submit。"""

    def __init__(
        self,
        container: str = "sparkos-spark-master",
        spark_bin: str = "/opt/spark/bin/spark-submit",
        master_url: str = "spark://spark-master:7077",
    ) -> None:
        self.container = container
        self.spark_bin = spark_bin
        self.master_url = master_url

    def submit(
        self,
        job_name: str,
        code: str,
        *,
        job_type: str = "pyspark",
        executor_memory: str = "2g",
        executor_cores: int = 2,
        num_executors: int = 2,
        driver_memory: str = "2g",
    ) -> SparkJobResult:
        """提交 PySpark 脚本到 Spark 集群。"""
        script_path = f"/opt/sparkos/tmp_{job_name}.py"
        cmd = [
            "docker", "exec", self.container,
            "sh", "-c",
            f"cat > {script_path} << 'PYEOF'\n{code}\nPYEOF\n"
            f"{self.spark_bin}"
            f" --master {self.master_url}"
            f" --name {job_name}"
            f" --deploy-mode client"
            f" --driver-memory {driver_memory}"
            f" --executor-memory {executor_memory}"
            f" --executor-cores {executor_cores}"
            f" --num-executors {num_executors}"
            f" {script_path}"
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return SparkJobResult(job_id="", status="timeout", output="任务提交超时（10 分钟）")
        except Exception as exc:  # noqa: BLE001
            return SparkJobResult(job_id="", status="error", output=f"提交失败: {exc}")

        output = (proc.stdout or proc.stderr or "").strip()
        combined = "\n".join(filter(None, [proc.stderr, proc.stdout])).strip()
        job_id = self._extract_job_id(combined)
        status = "submitted" if job_id else "failed"

        return SparkJobResult(job_id=job_id, status=status, output=output)

    def get_status(self, job_id: str) -> SparkJobResult:
        """查询任务状态。"""
        cmd = [
            "docker", "exec", self.container,
            self.spark_bin,
            "--status", job_id,
            "--master", self.master_url,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            return SparkJobResult(job_id=job_id, status="error", output=f"查询失败: {exc}")

        output = (proc.stdout or proc.stderr or "").strip()
        status = self._parse_status(output)
        return SparkJobResult(job_id=job_id, status=status, output=output)

    def get_logs(self, job_id: str) -> SparkJobResult:
        """获取任务日志（通过 docker logs 查看容器输出）。"""
        cmd = [
            "docker", "logs", self.container,
            "--tail", "200",
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            return SparkJobResult(job_id=job_id, status="error", output=f"获取日志失败: {exc}")

        output = (proc.stdout or proc.stderr or "").strip()
        # 只返回包含 job_id 相关的日志片段
        relevant = "\n".join(
            line for line in output.splitlines()
            if job_id in line or "INFO" in line or "ERROR" in line or "WARN" in line
        )
        return SparkJobResult(job_id=job_id, status="ok", output=relevant or output or "无日志输出")

    @staticmethod
    def _extract_job_id(text: str) -> str:
        """从 spark-submit 输出中提取 application_id / app_id。"""
        for line in text.splitlines():
            # Standalone: Connected to Spark cluster with app ID app-xxx
            if "app ID" in line:
                parts = line.split("app ID")
                if len(parts) > 1:
                    candidate = parts[-1].strip().split()[0].strip()
                    if candidate.startswith("app-") or candidate.startswith("application_"):
                        return candidate
            # 直接查找 app-xxx 或 application_xxx
            for part in line.split():
                if part.startswith("app-") or part.startswith("application_"):
                    return part.strip()
        return ""

    @staticmethod
    def _parse_status(text: str) -> str:
        normalized = text.lower()
        if "finished" in normalized or "success" in normalized:
            return "success"
        if "running" in normalized:
            return "running"
        if "lost" in normalized or "failed" in normalized:
            return "failed"
        if "pending" in normalized or "waiting" in normalized:
            return "pending"
        return "unknown"
