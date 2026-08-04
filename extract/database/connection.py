# database/connection.py
import logging

from google.cloud import bigquery
from google.cloud.exceptions import NotFound

from extract.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class BigQueryConnection:
    """BigQuery 프로젝트·데이터셋 연결과 리소스 보장.

    인증은 GOOGLE_APPLICATION_CREDENTIALS 또는 `gcloud auth application-default
    login` 로 잡힌 ADC(Application Default Credentials)를 그대로 사용한다.
    """

    def __init__(
        self,
        project: str = settings.BQ_PROJECT,
        dataset: str = settings.BQ_DATASET,
        location: str = settings.BQ_LOCATION,
    ):
        self.project = project
        self.dataset = dataset
        self.location = location
        self.client = bigquery.Client(project=project, location=location)

    @property
    def dataset_ref(self) -> bigquery.DatasetReference:
        return bigquery.DatasetReference(self.project, self.dataset)

    def table_id(self, name: str) -> str:
        """`agriquant.agriculture.<name>` 형태의 완전 수식 테이블 ID."""
        return f"{self.project}.{self.dataset}.{name}"

    def ensure_dataset(self) -> bigquery.DatasetReference:
        """데이터셋이 없으면 지정 리전에 생성한다(멱등)."""
        ref = self.dataset_ref
        try:
            self.client.get_dataset(ref)
        except NotFound:
            ds = bigquery.Dataset(ref)
            ds.location = self.location
            self.client.create_dataset(ds, exists_ok=True)
            logging.info(
                f"[BigQuery] 데이터셋 생성: {self.project}.{self.dataset} ({self.location})"
            )
        return ref
