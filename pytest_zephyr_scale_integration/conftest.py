import re
import time
import pytest
from pprint import pprint

from .integration import Integration
from .utils import get_or_create_folder
import logging


logger = logging.getLogger('zephyr_integration')

# --------------------
# Globals for Zephyr integration
executed_test_keys = []            # list of test case keys (e.g., ['T123', 'T234'])
parametrized_test_keys = set()     # set of keys for parameterized tests (e.g., {'T123'})
full_test_results = {}             # map nodeid -> status_id
set_test_results = {}              # map test case key -> status_id
dict_test_statuses = {}            # map 'PASS'/'FAIL' -> status_id
# --------------------

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Сбор результатов и определение параметризованных тестов"""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        test_nodeid = item.nodeid
        match = re.search(r"T\d+", test_nodeid)
        if match:
            key = match.group(0)
            if key not in executed_test_keys:
                executed_test_keys.append(key)

            # определяем параметризованные тесты через pytest
            if hasattr(item, 'callspec'):
                parametrized_test_keys.add(key)

            # сохраняем результат для каждого nodeid
            status_id = dict_test_statuses.get('PASS') if report.passed else dict_test_statuses.get('FAIL')
            full_test_results[test_nodeid] = status_id

            # агрегируем статус тест-кейса (FAIL преобладает)
            prev = set_test_results.get(key)
            if prev is None or status_id == dict_test_statuses.get('FAIL'):
                set_test_results[key] = status_id

    # отладочный вывод (при необходимости)
    # pprint({
    #     'executed_test_keys': executed_test_keys,
    #     'parametrized_test_keys': list(parametrized_test_keys),
    #     'set_test_results': set_test_results,
    #     'dict_test_statuses': dict_test_statuses,
    # })


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_sessionfinish(session, exitstatus):
    """Отправка результатов в Zephyr Scale после выполнения сессии"""
    yield
    api_start = time.time()

    zephyr_enabled = getattr(session.config, '_zephyr_enabled', False)
    integration = getattr(session.config, '_zephyr_integration', None)
    user_email = getattr(session.config, '_executed_by_email', None)
    test_run_name = getattr(session.config, '_zephyr_test_run_name', 'Test Run Cycle')
    test_run_id = getattr(session.config, '_zephyr_test_run_id', None)

    if not zephyr_enabled or integration is None:
        return

    user_key = integration.get_user_key_by_email(user_email)

    project_key = integration.get_project_key_by_project_id()
    folder_id = None
    if integration.folder_name:
        folders = integration.get_test_run_folders()
        folder_id = get_or_create_folder(integration, folders, integration.folder_name)

    if not test_run_id:
        # Создаем тест-цикл
        test_run_id = integration.create_test_cycle(test_run_name, folder_id)
        print(f"Test run created: {test_run_id}")

        # Добавляем тест-кейсы в тест-цикл
        case_ids = [integration.get_test_case_id(project_key, key) for key in executed_test_keys]
        integration.add_test_cases_to_cycle(test_run_id, case_ids, user_key)

    # Получаем элементы тест-рана
    test_run_items = integration.get_test_run_items(test_run_id)

    # Обновляем статусы тест-кейсов
    case_payload = []
    for item in test_run_items:
        key = item['$lastTestResult']['testCase']['key'].split('-')[-1]
        if key in set_test_results:
            # Базовый объект для обновления
            entry = {
                'id': item['$lastTestResult']['id'],
                'testResultStatusId': set_test_results[key]
            }
            # Добавляем executed by, если есть user_key
            if user_key:
                entry['userKey'] = user_key
            case_payload.append(entry)

    if case_payload:
        integration.set_test_case_statuses(case_payload)

    # Обновляем шаги только для параметризованных тестов
    for item in test_run_items:
        key = item['$lastTestResult']['testCase']['key'].split('-')[-1]
        if key not in parametrized_test_keys:
            continue

        # Получаем результаты шагов (возвращает список)
        script_results = integration.get_test_script_results(test_run_id, item['id'])
        first = script_results[0] if isinstance(script_results, list) and script_results else {}

        # Группируем шаги по parameterSetId
        ps_map = {}
        for entry in first.get('testScriptResults', []):
            pid = entry.get('parameterSetId')
            sid = entry.get('id')
            if pid:
                ps_map.setdefault(pid, []).append(sid)

        # Детерминированная сортировка
        sorted_ps = dict(sorted(ps_map.items()))

        # Берём соответствующие статусы из pytest
        results = [status for nodeid, status in full_test_results.items() if key in nodeid]

        # Формируем payload для шагов
        steps_payload = []
        for (pid, steps), status in zip(sorted_ps.items(), results):
            for sid in steps:
                steps_payload.append({
                    'id': sid,
                    'testResultStatusId': status
                })
        if steps_payload:
            integration.set_test_script_statuses(steps_payload)

    # Логирование статистики
    total = len(executed_test_keys)
    param = len(parametrized_test_keys)
    logger.info(f"Total test cases: {total}, Parameterized: {param}")
    # Таблица запросов
    logger.info("Request counts:")
    for ep, cnt in integration.request_counts.items():
        logger.info(f"  {ep}: {cnt}")
    # Логирование длительности интеграции
    duration = time.time() - integration.start_time
    logger.info(f"Integration total duration with tests: {duration:.2f} seconds")
    api_duration = time.time() - api_start
    logger.info(f"Zephyr API push duration: {api_duration:.2f} seconds")


@pytest.hookimpl
def pytest_configure(config):
    """Конфигурация"""

    zephyr_enabled = config.getoption("--zephyr", default=False)
    zephyr_test_run_name = config.getoption("--zephyr_test_run_name", default="Test Run Cycle")
    zephyr_test_run_id = config.getoption("--zephyr_test_run_id")
    jira_token = config.getoption("--jira_token")

    if zephyr_enabled and not jira_token:
        raise ValueError("Для интеграции с Zephyr необходимо передать параметр --jira_token.")

    # Сохраняем значения в config для использования в pytest_sessionfinish
    config._zephyr_enabled = zephyr_enabled
    config._zephyr_test_run_name = zephyr_test_run_name
    config._zephyr_test_run_id = zephyr_test_run_id
    config._jira_token = jira_token
    config._executed_by_email = config.getoption("--executed_by_email")

    # если флаг --zephyr установлен
    if zephyr_enabled:
        integration = Integration(jira_token, cache=config.cache)
        integration.load_environment_variables()

        # Получаем статусы тестов и сохраняем их в dict_test_statuses
        status_items = integration.get_test_statuses()
        for status_item in status_items:
            status = status_item.get('name').upper()
            if status not in dict_test_statuses:
                dict_test_statuses[status] = status_item.get('id')

        # Сохраняем данные в config, чтобы использовать их в других хуках
        config._zephyr_integration = integration
        config._zephyr_test_run_name = zephyr_test_run_name


def pytest_addoption(parser):
    """Кастомные параметры запуска автотестов."""
    parser.addoption("--zephyr", action="store_true", help="Enable Zephyr integration")
    parser.addoption("--zephyr_test_run_name", action="store", default="Test Run Cycle",
                     help="Name of the test run cycle")
    parser.addoption("--zephyr_test_run_id", action="store", default=None,
                     help="Use existing Zephyr test run ID instead of creating a new one")
    parser.addoption("--jira_token", action="store", help="JIRA API token for authentication")
    parser.addoption("--executed_by_email", action="store", default=None, help="User email")
