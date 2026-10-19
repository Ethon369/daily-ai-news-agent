import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def find_bash() -> str:
    if os.name == 'nt':
        git = shutil.which('git')
        candidates = [Path(git).parent.parent / 'bin/bash.exe'] if git else []
        candidates += [Path('C:/Program Files/Git/bin/bash.exe')]
        return next((str(p) for p in candidates if p.is_file()), '')
    return shutil.which('bash') or ''


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: ROOT / 'data')
    api_key: str = ''
    base_url: str = 'https://api.deepseek.com'
    model: str = 'deepseek-flash'
    smtp_host: str = 'smtp.qq.com'
    smtp_port: int = 465
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_from: str = ''
    bash_path: str = field(default_factory=find_bash)
    max_rounds: int = 12
    max_tools: int = 24
    agent_timeout: float = 180
    scheduler_enabled: bool = True

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / '.env', override=False)
        settings = cls(
            data_dir=Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve(),
            api_key=os.getenv('DEEPSEEK_API_KEY', ''),
            base_url=os.getenv('DEEPSEEK_BASE_URL', 'https://api.deepseek.com').rstrip('/'),
            model=os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'),
            smtp_host=os.getenv('SMTP_HOST', 'smtp.qq.com'),
            smtp_port=int(os.getenv('SMTP_PORT', '465')),
            smtp_username=os.getenv('SMTP_USERNAME', ''),
            smtp_password=os.getenv('SMTP_PASSWORD', ''),
            smtp_from=os.getenv('SMTP_FROM', '') or os.getenv('SMTP_USERNAME', ''),
            bash_path=os.getenv('BASH_PATH') or find_bash(),
            max_rounds=int(os.getenv('AGENT_MAX_ROUNDS', '12')),
            max_tools=int(os.getenv('AGENT_MAX_TOOLS', '24')),
            agent_timeout=float(os.getenv('AGENT_TIMEOUT', '180')),
            scheduler_enabled=os.getenv('SCHEDULER_ENABLED', 'true').lower() == 'true',
        )
        if not (1 <= settings.max_rounds <= 50 and 1 <= settings.max_tools <= 100
                and 1 <= settings.agent_timeout <= 600):
            raise ValueError('Agent budget configuration is outside supported bounds')
        return settings

    @property
    def smtp_ready(self):
        return all([self.smtp_username, self.smtp_password, self.smtp_from])
