import os
import time

import requests
from dotenv import load_dotenv
from requests import HTTPError
from requests_toolbelt.utils import dump

# Загрузка переменных из .env файла
# load_dotenv()

# Получение значений из переменных окружения
NOT_EXECUTED = None
IN_PROGRESS = None
PASS = None
FAIL = None
BLOCKED = None

JIRA_TOKEN = None
JIRA_PROJECT_NAME = None
JIRA_PROJECT_ID = None
JIRA_URL = None


class Integration:
    def __init__(self):
        self.session = requests.Session()
        self.max_retries = 5  # Максимальное количество повторных попыток
        self.retry_delay = 1  # Начальная задержка перед повторной попыткой (в секундах)

        self.NOT_EXECUTED = None
        self.IN_PROGRESS = None
        self.PASS = None
        self.FAIL = None
        self.BLOCKED = None

        self.JIRA_TOKEN = None
        self.JIRA_PROJECT_NAME = None
        self.JIRA_PROJECT_ID = None
        self.JIRA_URL = None

        # self.api_token = api_token
        # self.base_url = base_url
        # self.project_id = project_id
        # self.project_name = project_name

        # self.session = requests.Session()
        # self.session.headers.update({
        #     'Authorization': f'Bearer {JIRA_TOKEN}',
        #     'Content-Type': 'application/json'
        # })

    def load_environment_variables(self):
        # Загрузка переменных из .env файла
        load_dotenv()

        # Получение значений из переменных окружения
        self.NOT_EXECUTED = int(os.getenv("NOT_EXECUTED"))
        self.IN_PROGRESS = int(os.getenv("IN_PROGRESS"))
        self.PASS = int(os.getenv("PASS"))
        self.FAIL = int(os.getenv("FAIL"))
        self.BLOCKED = int(os.getenv("BLOCKED"))

        self.JIRA_TOKEN = os.getenv("JIRA_TOKEN")
        self.JIRA_PROJECT_NAME = os.getenv("JIRA_PROJECT_NAME")
        self.JIRA_PROJECT_ID = int(os.getenv("JIRA_PROJECT_ID"))
        self.JIRA_URL = os.getenv("JIRA_URL")

        # Проверяем, что все необходимые переменные окружения заданы
        missing_env_vars = []
        if not self.JIRA_TOKEN:
            missing_env_vars.append("JIRA_TOKEN")
        if not self.JIRA_PROJECT_NAME:
            missing_env_vars.append("JIRA_PROJECT_NAME")
        if not self.JIRA_PROJECT_ID:
            missing_env_vars.append("JIRA_PROJECT_ID")
        if not self.JIRA_URL:
            missing_env_vars.append("JIRA_URL")
        if not self.PASS:
            missing_env_vars.append("PASS")
        if not self.FAIL:
            missing_env_vars.append("FAIL")

        if missing_env_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_env_vars)}")
        else:
            print(f'Переменные загружены: {self.JIRA_TOKEN} \t {self.JIRA_PROJECT_ID} \t {self.JIRA_URL} \t {self.JIRA_PROJECT_ID}')

        # Устанавливаем заголовки после загрузки переменных
        self.session.headers.update({
            'Authorization': f'Bearer {self.JIRA_TOKEN}',
            'Content-Type': 'application/json'
        })

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
        url = f"{self.JIRA_URL}/rest/tests/1.0/testrun"
        payload = {
            "name": cycle_name,
            "projectId": self.JIRA_PROJECT_ID,
            "statusId": 3247
        }
        response = self.session.post(url, json=payload)
        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')  # Возвращает ID созданного цикла тестов

    def get_test_case_id(self, test_case_key):
        url = f"{self.JIRA_URL}/rest/tests/1.0/testcase/{self.JIRA_PROJECT_NAME}-{test_case_key}?fields=id"
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')

    def get_test_run_id(self, test_cycle_key):
        url = f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_cycle_key}?fields=id"
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('id')

    def add_test_cases_to_cycle(self, test_run_id, test_case_ids):
        url = f"{self.JIRA_URL}/rest/tests/1.0/testrunitem/bulk/save"
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
        url = (f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_run_id}/testrunitems?"
               f"fields=testCaseId,testScriptResults(id),testRunId")
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json().get('testRunItems', [])

    def get_test_script_results(self, test_run_id, item_id):
        url = (f"{self.JIRA_URL}/rest/tests/1.0/testrun/{test_run_id}"
               f"/testresults?fields=testScriptResults(id,parameterSetId)&itemId={item_id}")
        response = self._send_request_with_retries('GET', url)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
        return response.json()

    def set_test_case_statuses(self, statuses):
        url = f"{self.JIRA_URL}/rest/tests/1.0/testresult"
        response = self._send_request_with_retries('PUT', url, json=statuses)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()

    def set_test_script_statuses(self, script_statuses):
        url = f"{self.JIRA_URL}/rest/tests/1.0/testscriptresult"
        response = self._send_request_with_retries('PUT', url, json=script_statuses)

        data = dump.dump_all(response)
        print(data.decode('utf-8'))

        response.raise_for_status()
