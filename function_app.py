import azure.functions as func
import datetime
import json
import logging
import requests
import os
from azure.data.tables import TableClient
from azure.core.exceptions import ResourceNotFoundError
from typing import List, Dict


# Configuration class for storing settings
class Config:
    def __init__(self):
        self.storage_conn_string = None
        self.table_name = None
        self.timeout_seconds = None

    @staticmethod
    def load_from_settings() -> "Config":
        config = Config()
        config.storage_conn_string = os.environ["StorageAccountConnectionString"]
        config.table_name = os.environ["TableName"]
        config.timeout_seconds = 10
        return config


class UrlHealthChecker:
    def __init__(self, config: Config):
        self.config = config
        self.table_client = TableClient.from_connection_string(
            config.storage_conn_string, config.table_name
        )

    def get_urls_to_check(self) -> List[Dict]:
        try:
            entities = self.table_client.list_entities()
            return [
                {
                    "url": entity["url"],
                    "id": entity["PartitionKey"],
                    "expected_status": "200",
                }
                for entity in entities
            ]
        except ResourceNotFoundError:
            logging.error(f"Table {self.config.table_name} not found")
            return []

    def check_url(self, url: str, expected_status: int = 200) -> Dict:
        try:
            response = requests.get(
                url, timeout=self.config.timeout_seconds, verify=True
            )
            return {
                "url": url,
                "status_code": response.status_code,
                "response_time_ms": round(response.elapsed.total_seconds() * 1000),
                "is_healthy": response.status_code == expected_status,
                "checked_at": datetime.datetime.utcnow().isoformat(),
            }
        except requests.exceptions.RequestException as e:
            return {
                "url": url,
                "status_code": None,
                "response_time_ms": None,
                "is_healthy": False,
                "error": str(e),
                "checked_at": datetime.datetime.utcnow().isoformat(),
            }

    def update_health_status(self, health_result: Dict):
        self.table_client.update_entity(
            {
                "PartitionKey": "",
                "RowKey": "",
                "url": health_result["url"],
                "last_check_result": json.dumps(health_result),
                "last_checked_at": health_result["checked_at"],
            }
        )


app = func.FunctionApp()


@app.schedule(
    schedule="*/5 * * * *", arg_name="timer", run_on_startup=True, use_monitor=False
)
def check_urls_health(timer: func.TimerRequest) -> None:
    # Load configuration
    config = Config.load_from_settings()

    # Initialize checker
    checker = UrlHealthChecker(config)

    # Get URLs to check
    urls = checker.get_urls_to_check()

    if not urls:
        logging.warning("No URLs found to check")
        return

    # Check each URL and update status
    for url_info in urls:
        result = checker.check_url(url_info["url"], url_info["expected_status"])
        checker.update_health_status(result)

        log_level = logging.INFO if result["is_healthy"] else logging.ERROR
        logging.log(
            log_level,
            f"Health check result for {url_info['url']}: {json.dumps(result)}",
        )
