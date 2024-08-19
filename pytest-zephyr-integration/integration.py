import os
import time

import requests
from dotenv import load_dotenv
from requests import HTTPError
from requests_toolbelt.utils import dump

# Загрузка переменных из .env файла
load_dotenv()

# Получение значений из переменных окружения
NOT_EXECUTED = int(os.getenv("NOT_EXECUTED"))
IN_PROGRESS = int(os.getenv("IN_PROGRESS"))
PASS = int(os.getenv("PASS"))
FAIL = int(os.getenv("FAIL"))
BLOCKED = int(os.getenv("BLOCKED"))

JIRA_TOKEN = os.getenv("JIRA_TOKEN")
JIRA_PROJECT_NAME = os.getenv("JIRA_PROJECT_NAME")
JIRA_PROJECT_ID = int(os.getenv("JIRA_PROJECT_ID"))
JIRA_URL = os.getenv("JIRA_URL")


class Integration:
    def __init__(self, api_token, base_url, project_id, project_name):
        self.api_token = api_token
        self.base_url = base_url
        self.project_id = project_id
        self.project_name = project_name
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        })

        self.max_retries = 5  # Максимальное количество повторных попыток
        self.retry_delay = 1  # Начальная задержка перед повторной попыткой (в секундах)

    def _send_request_with_retries(self, method, url, **kwargs):
        retries = 0
        while retries < self.max_retries:
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 429:
                retries += 1
                wait_time = self.retry_delay * (2 ** (retries - 1))  # Экспоненциальная задержка
                print(f"Превышен лимит количества отправленный сообщений. "
                      f"Ожидаю {wait_time} секунд до повторной отправки...")
                time.sleep(wait_time)
            else:
                response.raise_for_status()
                return response
        raise HTTPError(f"Не удалось выполнить запрос после {self.max_retries} "
                        f"попыток из-за ограничений скорости отправки запросов.")

    def create_test_cycle(self, cycle_name):
        url = f"{self.base_url}/rest/tests/1.0/testrun"
        payload = {
            "name": cycle_name,
            "projectId": self.project_id,
            "statusId": 3247
        }
        response = self.session.post(url, json=payload)
        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')  # Возвращает ID созданного цикла тестов

    def get_test_case_id(self, test_case_key):
        url = f"{self.base_url}/rest/tests/1.0/testcase/{self.project_name}-{test_case_key}?fields=id"
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')

    def get_test_run_id(self, test_cycle_key):
        url = f"{self.base_url}/rest/tests/1.0/testrun/{test_cycle_key}?fields=id"
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')

    def add_test_cases_to_cycle(self, test_run_id, test_case_ids):
        url = f"{self.base_url}/rest/tests/1.0/testrunitem/bulk/save"
        added_test_run_items = [
            {"index": i, "lastTestResult": {"testCaseId": test_case_id}}
            for i, test_case_id in enumerate(test_case_ids)
        ]
        payload = {
            "testRunId": test_run_id,
            "addedTestRunItems": added_test_run_items
        }
        response = self._send_request_with_retries('PUT', url, json=payload)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()

    def get_test_run_items(self, test_run_id):
        url = (f"{self.base_url}/rest/tests/1.0/testrun/{test_run_id}/testrunitems?"
               f"fields=testCaseId,testScriptResults(id),testRunId")
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('testRunItems', [])

    def get_test_script_results(self, test_run_id, item_id):
        url = (f"{self.base_url}/rest/tests/1.0/testrun/{test_run_id}"
               f"/testresults?fields=testScriptResults(id,parameterSetId)&itemId={item_id}")
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json()

    def set_test_case_statuses(self, statuses):
        url = f"{self.base_url}/rest/tests/1.0/testresult"
        response = self._send_request_with_retries('PUT', url, json=statuses)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()

    def set_test_script_statuses(self, script_statuses):
        url = f"{self.base_url}/rest/tests/1.0/testscriptresult"
        response = self._send_request_with_retries('PUT', url, json=script_statuses)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
