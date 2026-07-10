from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "金融知识问答助手"
    data_dir: Path = Path("./storage")
    vector_db_path: Path = Path("./storage/vector_store.sqlite3")

    top_k: int = 5
    min_score: float = 0.35

    embedding_model_path: Path = Path(r"D:\1求职\1\fault-diagnosis-agent\bge-m3")
    embedding_device: str = "cpu"
    embedding_batch_size: int = 8
    query_instruction: str = "Represent this sentence for searching relevant passages: "

    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.vector_db_path.parent.mkdir(parents=True, exist_ok=True)
