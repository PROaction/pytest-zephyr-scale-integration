from pprint import pprint
import re

import pytest

from .integration import Integration
from .utils import get_or_create_folder


executed_test_keys = []  # список выполненных тестов (pytest)
full_test_results = {}  # словарь со всеми тестами и статусами (вместе с параметризацией)
set_test_results = {}  # словарь с тестами и статусаим без повторений (для установки устатуса для теста)
dict_test_statuses = {}  # словарь со статусами для тест-кейсов (например, {'PASS': 3238, 'FAIL': 3239})


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Получение результатов прогона автотестов
    """
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        test_nodeid = item.nodeid
        test_key_match = re.search(r"T\d+", test_nodeid)

        if test_key_match:
            potential_key = test_key_match.group(0)

            if potential_key not in executed_test_keys:
                executed_test_keys.append(potential_key)

            # Создание словаря со статусами test_key: status_id
            # {'T123': 3238, 'T234': 3239}
            if report.outcome == "passed":
                full_test_results[test_nodeid] = dict_test_statuses.get('PASS')

                # Если тест параметризованный, то одинаковых ключей будет несколько.
                # Если хотя бы один из них FAIL, то FAIL.
                if potential_key not in set_test_results:
                    set_test_results[potential_key] = dict_test_statuses.get('PASS')
            else:
                full_test_results[test_nodeid] = dict_test_statuses.get('FAIL')
                set_test_results[potential_key] = dict_test_statuses.get('FAIL')

    print('executed_test_keys:', executed_test_keys)
    print('full_test_results:', full_test_results)
    print('set_test_results:', set_test_results)
    print('dict_test_statuses:', dict_test_statuses)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Конфигурация"""

    zephyr_enabled = config.getoption("--zephyr", default=False)
    test_run_name = config.getoption("--test_run_name", default="Test Run Cycle")

    # Сохраняем значения в config для использования в pytest_sessionfinish
    config._zephyr_enabled = zephyr_enabled
    config._test_run_name = test_run_name

    # если флаг --zephyr установлен
    if zephyr_enabled:
        integration = Integration()
        integration.load_environment_variables()

        # Получаем статусы тестов и сохраняем их в dict_test_statuses
        status_items = integration.get_test_statuses()
        for status_item in status_items:
            status = status_item.get('name').upper()
            if status not in dict_test_statuses:
                dict_test_statuses[status] = status_item.get('id')

        # Сохраняем данные в config, чтобы использовать их в других хуках
        config._zephyr_integration = integration
        config._test_run_name = test_run_name


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_sessionfinish(session, exitstatus):
    """Обертка для выполнения действий до и после основного кода хука"""

    yield

    # Получаем сохраненные данные из config
    zephyr_enabled = getattr(session.config, "_zephyr_enabled", False)
    test_run_name = getattr(session.config, "_test_run_name", "Test Run Cycle")
    integration = getattr(session.config, "_zephyr_integration", None)
    folder_name = integration.folder_name

    if zephyr_enabled and integration:
        project_key = integration.get_project_key_by_project_id()

        test_run_id = None
        folder_id = None
        if folder_name:
            folders = integration.get_test_run_folders()
            folder_id = get_or_create_folder(integration, folders, folder_name)

        test_run_id = integration.create_test_cycle(test_run_name, folder_id)
        print('Тестовый цикл создан:', test_run_id)

        # Добавление тест-кейсов в тестовый цикл
        test_case_ids = [integration.get_test_case_id(project_key, test_case_key) for test_case_key in
                         executed_test_keys]
        integration.add_test_cases_to_cycle(test_run_id, test_case_ids)

        # Получаем список тестов в цикле с их ID
        test_run_items = integration.get_test_run_items(test_run_id)

        # В словаре с тест-кейсами и их статусами заменяем ключ вида T123 на ID ["$lastTestResult"]["id"]
        updated_test_results = {}
        for item in test_run_items:
            test_case_key = item["$lastTestResult"]["testCase"]["key"].split('-')[-1]
            if test_case_key in set_test_results:
                updated_test_results[item['$lastTestResult']["id"]] = set_test_results[test_case_key]

        # Обновление статуса тест-кейсов
        if updated_test_results:
            statuses_to_update = [{"id": k, "testResultStatusId": v} for k, v in updated_test_results.items()]
            integration.set_test_case_statuses(statuses_to_update)

        # Обработка параметризованных тестов
        for test_key in executed_test_keys:
            relevant_results = [result for key, result in full_test_results.items() if test_key in key]
            print('relevant_results')
            pprint(relevant_results)

            for item in test_run_items:
                test_case_run_id = item["id"]
                script_results = integration.get_test_script_results(test_run_id, test_case_run_id)

                parameter_set_status = {}
                for script_result in script_results[0]["testScriptResults"]:
                    parameter_set_id = script_result.get('parameterSetId')
                    if parameter_set_id:
                        if parameter_set_id not in parameter_set_status:
                            parameter_set_status[parameter_set_id] = {"status": None, "steps": []}
                        parameter_set_status[parameter_set_id]["steps"].append(script_result['id'])

                sorted_parameter_set_status = dict(sorted(parameter_set_status.items()))
                print('sorted_parameter_set_status')
                print(sorted_parameter_set_status)

                for param_id, info in zip(sorted_parameter_set_status.keys(), relevant_results):
                    sorted_parameter_set_status[param_id]["status"] = info

                script_statuses_to_update = []
                for param_id, info in sorted_parameter_set_status.items():
                    for step_id in info["steps"]:
                        script_statuses_to_update.append({
                            "id": step_id,
                            "testResultStatusId": info["status"]
                        })

                print('script_statuses_to_update')
                print(script_statuses_to_update)

                if script_statuses_to_update:
                    integration.set_test_script_statuses(script_statuses_to_update)


def pytest_addoption(parser):
    """Кастомные параметры запуска автотестов."""
    parser.addoption("--zephyr", action="store_true", help="Enable Zephyr integration")
    parser.addoption("--test_run_name", action="store", default="Test Run Cycle", help="Name of the test run cycle")
