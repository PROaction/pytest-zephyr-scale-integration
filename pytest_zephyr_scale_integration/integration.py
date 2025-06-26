import os
import time
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
import requests
from requests import HTTPError
from requests_toolbelt.utils import dump

from .zephyr_logger import setup_logger

JIRA_TOKEN = None
JIRA_PROJECT_ID = None
JIRA_URL = None


class Integration:
    def __init__(self, jira_token, cache=None):
        self.logger = setup_logger()
        self.logger.info('Initializing Zephyr Integration')
        self.start_time = time.time()

        self.session = requests.Session()
        self.max_retries = 5
        self.retry_delay = 1
        # Track number of requests per endpoint
        self.request_counts = {}  # endpoint -> count

        # Added: in-memory cache for test case IDs
        self._tcid_cache = {}
        # Added: pytest cache plugin instance passed in constructor
        self._cache = cache

        self.JIRA_TOKEN = jira_token
        self.JIRA_PROJECT_ID = None
        self.JIRA_URL = None
        self.folder_name = None

        self.session.headers.update({
            'Authorization': f'Bearer {self.JIRA_TOKEN}',
            'Content-Type': 'application/json'
        })

    def load_environment_variables(self):
        """Загрузка переменных окружения из .env файла"""

        load_dotenv()

        # Получение значений из переменных окружения
        self.JIRA_PROJECT_ID = int(os.getenv("JIRA_PROJECT_ID"))
        self.JIRA_URL = os.getenv("JIRA_URL")
        self.folder_name = os.getenv("FOLDER_NAME", None)

        # Проверка на наличие обязательных переменных
        missing_env_vars = [var for var in ["JIRA_TOKEN", "JIRA_PROJECT_ID", "JIRA_URL"] if not getattr(self, var)]
        if missing_env_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_env_vars)}")
        else:
            print(
                f'Переменные загружены: {self.JIRA_TOKEN} \t {self.JIRA_PROJECT_ID} \t '
                f'{self.JIRA_URL} \t {self.JIRA_PROJECT_ID}')

    def _send_request_with_retries(self, method, url, **kwargs):
        endpoint = url.split('?')[0]
        self.request_counts.setdefault(endpoint, 0)
        retries = 0
        while retries < self.max_retries:
            self.request_counts[endpoint] += 1
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 429:
                wait_time = self.retry_delay * (2 ** retries)
                self.logger.warning(f'Rate limit hit on {endpoint}, retry in {wait_time}s')
                time.sleep(wait_time)
                retries += 1
            else:
                try:
                    response.raise_for_status()
                except HTTPError as e:
                    # Log request and response details on error
                    req = response.request
                    self.logger.error(
                        f"Request to {req.method} {req.url} failed.\nHeaders: {req.headers}\nBody: {req.body}"
                    )
                    self.logger.error(
                        f"Response status: {response.status_code}\nResponse body: {response.text}"
                    )
                    raise
                return response
        msg = f'Failed after {self.max_retries} retries: {endpoint}'
        self.logger.error(msg)
        raise HTTPError(msg)

    def get_project_key_by_project_id(self):
        """Получение ключа проекта по его ID"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/project/{self.JIRA_PROJECT_ID}"
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/project/{self.JIRA_PROJECT_ID}, status {response.status_code}')
        response.raise_for_status()

        return response.json().get('key')

    def create_test_cycle(self, cycle_name, folder_id=None):
        """Создание тестового цикла"""
        test_cycle_statuses = self.get_test_cycle_statuses()
        test_cycle_status_id = None
        for test_cycle_status in test_cycle_statuses:
            if test_cycle_status.get('name').lower() == 'not executed':
                test_cycle_status_id = test_cycle_status.get('id')

        now_utc = datetime.now(timezone.utc)
        formatted_time = now_utc.isoformat(timespec='milliseconds').replace('+00:00', 'Z')

        url = f"{self.JIRA_URL}/rest/tests/1.0/testrun"
        payload = {
            "name": cycle_name,
            "projectId": self.JIRA_PROJECT_ID,
            "statusId": test_cycle_status_id if not test_cycle_status_id else test_cycle_statuses[0].get('id'),
            "plannedStartDate": formatted_time,
            "plannedEndDate": formatted_time
        }

        if folder_id:
            payload["folderId"] = folder_id

        response = self._send_request_with_retries('POST', url, json=payload)

        self.logger.info(f'POST /rest/tests/1.0/testrun, status {response.status_code}')
        response.raise_for_status()

        test_run_id = response.json().get('id')  # ID созданного тестового цикла

        # Сохраняем test_run_id в кэш pytest (.pytest_cache/v/zephyr/test_run_id)
        if self._cache:
            self._cache.set("zephyr/test_run_id", test_run_id)
            self.logger.info(f"Saved test_run_id={test_run_id} to pytest cache")
        else:
            self.logger.warning("Pytest cache is not available; test_run_id not cached")

        return test_run_id

    def create_test_run_folder(self, folder_name):
        """Создание новой папки для тестового цикла"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/folder/testrun"
        payload = {
            "name": folder_name,
            "projectId": self.JIRA_PROJECT_ID,
            "index": 0
        }
        response = self._send_request_with_retries('POST', url, json=payload)

        self.logger.info(f'POST /rest/tests/1.0/folder/testrun, status {response.status_code}')

        response.raise_for_status()
        return response.json().get('id')  # Возвращаем ID новой папки

    def get_test_run_folders(self):
        """Получение дерева папок тестовых циклов"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/foldertree/testrun"
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/foldertree/testrun, '
                         f'status {response.status_code}')

        response.raise_for_status()

        return response.json()

    def get_test_case_id(self, project_key, test_case_key):
        """Получение ID тест-кейса по ключу проекта и ключу тест-кейса"""

        # Added: unique key for pytest cache storage
        cache_key = f"zephyr.testcase.{project_key}-{test_case_key}"

        # Step 1: check pytest cache
        if self._cache:
            cached = self._cache.get(cache_key, None)
            if cached:
                return cached

        # Step 2: check in-memory cache
        key = f"{project_key}-{test_case_key}"
        if key in self._tcid_cache:
            return self._tcid_cache[key]

        # If not cached, perform API request
        url = f"{self.JIRA_URL}/rest/tests/1.0/testcase/{project_key}-{test_case_key}?fields=id"
        response = self._send_request_with_retries('GET', url)
        tcid = response.json().get('id')

        # Added: store in both caches
        self._tcid_cache[key] = tcid
        if self._cache:
            self._cache.set(cache_key, tcid)

        self.logger.info(f'GET /rest/tests/1.0/testcase/{project_key}-{test_case_key}?fields=id, '
                         f'status {response.status_code}')

        response.raise_for_status()
        return tcid

    def get_test_run_id(self, test_cycle_key):
        """Получение ID тестового цикла"""
        url = f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_cycle_key}?fields=id"
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/testrun/{test_cycle_key}?fields=id, status {response.status_code}')

        response.raise_for_status()
        return response.json().get('id')

    def add_test_cases_to_cycle(self, test_run_id, test_case_ids, user_key=None):
        """Добавление тест-кейсов в тестовый цикл"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/testrunitem/bulk/save"

        if user_key:
            added_test_run_items = [
                {"index": i, "lastTestResult": {"testCaseId": test_case_id, "assignedTo": user_key}}
                for i, test_case_id in enumerate(test_case_ids)
            ]
        else:
            added_test_run_items = [
                {"index": i, "lastTestResult": {"testCaseId": test_case_id}}
                for i, test_case_id in enumerate(test_case_ids)
            ]
        payload = {
            "testRunId": test_run_id,
            "addedTestRunItems": added_test_run_items
        }
        response = self._send_request_with_retries('PUT', url, json=payload)

        self.logger.info(f'PUT /rest/tests/1.0/testrunitem/bulk/save, status {response.status_code}')

        response.raise_for_status()

    def get_test_run_items(self, test_run_id):
        """Получение всех тестов из тестового цикла"""

        url = (f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_run_id}/testrunitems?"
               f"fields=testCaseId,testScriptResults(id),testRunId")
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/testrun/{test_run_id}/testrunitems'
                         f'?fields=testCaseId,testScriptResults(id),testRunId, '
                         f'status {response.status_code}')

        response.raise_for_status()
        return response.json().get('testRunItems', [])

    def get_test_script_results(self, test_run_id, item_id):
        """Получение результатов тестовых скриптов (параметризованных ТК)"""

        url = (f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_run_id}"
               f"/testresults?fields=testScriptResults(id,parameterSetId)&itemId={item_id}")
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/testrun/{test_run_id}/testresults'
                         f'?fields=testScriptResults(id,parameterSetId)&itemId={item_id}, '
                         f'status {response.status_code}')

        response.raise_for_status()
        return response.json()

    def get_test_statuses(self):
        """Получение статусов для тест-кейсов"""

        url = f'{self.JIRA_URL}/rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/testresultstatus'
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/testresultstatus, '
                         f'status {response.status_code}')

        response.raise_for_status()
        return response.json()

    def get_test_cycle_statuses(self):
        """Получение статусов для тестового цикла"""
        url = f'{self.JIRA_URL}/rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/testrunstatus'
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'GET /rest/tests/1.0/project/{self.JIRA_PROJECT_ID}/testrunstatus, '
                         f'status {response.status_code}')

        response.raise_for_status()
        return response.json()

    def set_test_case_statuses(self, statuses, user_key=None):
        """Установка статусов для тест-кейсов"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/testresult"
        response = self._send_request_with_retries('PUT', url, json=statuses)

        self.logger.info(f'PUT /rest/tests/1.0/testresult, status {response.status_code}')

        response.raise_for_status()

    def set_test_script_statuses(self, script_statuses):
        """Установка статусов для шагов тест-кейсов"""

        url = f"{self.JIRA_URL}/rest/tests/1.0/testscriptresult"
        response = self._send_request_with_retries('PUT', url, json=script_statuses)

        self.logger.info(f'PUT /rest/tests/1.0/testscriptresult, status {response.status_code}')

        response.raise_for_status()

    def get_user_key_by_email(self, email: str) -> Optional[str]:
        """Получение Jira userKey по email"""
        url = f"{self.JIRA_URL}/rest/api/2/user/search?username={email}"
        response = self._send_request_with_retries('GET', url)

        self.logger.info(f'PUT /rest/api/2/user/search?username={email}, status {response.status_code}')

        response.raise_for_status()
        users = response.json()
        if users and isinstance(users, list):
            return users[0].get("key")  # например, JIRAUSERXXXXXX
        return None
