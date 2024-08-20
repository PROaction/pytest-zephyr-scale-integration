import re
from pprint import pprint

import pytest

from .integration import (FAIL, JIRA_PROJECT_ID, JIRA_PROJECT_NAME, JIRA_TOKEN,
                          JIRA_URL, PASS, Integration)

# Глобальный список для хранения ключей тестов, которые будут выполнены
executed_test_keys = []
full_test_results = {}  # полный список тестов (вместе с параметризацией)
# список тестов без повторений, для установки статусов у ТК
set_test_results = {}


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        test_nodeid = item.nodeid
        test_key_match = re.search(r"T\d+", test_nodeid)

        if test_key_match:
            potential_key = test_key_match.group(0)

            if potential_key not in executed_test_keys:
                executed_test_keys.append(potential_key)

            # Создание словаря test_key: status_id
            # {'T123': 3238, 'T234': 3239}
            if report.outcome == "passed":
                full_test_results[test_nodeid] = PASS
                # Если тест параметризованный, то одинаковых ключей будет несколько.
                # Если хотя бы один из них FAIL, то FAIL.
                if potential_key not in set_test_results:
                    set_test_results[potential_key] = PASS
            else:
                full_test_results[test_nodeid] = FAIL
                set_test_results[potential_key] = FAIL

    print('executed_test_keys')
    pprint(executed_test_keys)


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    zephyr_enabled = config.getoption("--zephyr", default=False)
    test_run_name = config.getoption("--test_run_name", default="Test Run Cycle")

    # Сохраняем значения в config для использования в pytest_sessionfinish
    config._zephyr_enabled = zephyr_enabled
    config._test_run_name = test_run_name

    if zephyr_enabled:
        integration = Integration()
        integration.load_environment_variables()  # Загружаем переменные только если флаг --zephyr установлен

        # Сохраняем данные в config, чтобы использовать их в других хуках
        config._zephyr_integration = integration
        config._test_run_name = test_run_name
        # config._executed_test_keys = []  # Это список, который будет заполняться по ходу выполнения тестов


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_sessionfinish(session, exitstatus):
    # Обертка для выполнения действий до и после основного кода хука
    outcome = yield

    # Получаем сохраненные данные из config
    zephyr_enabled = getattr(session.config, "_zephyr_enabled", False)
    test_run_name = getattr(session.config, "_test_run_name", "Test Run Cycle")
    integration = getattr(session.config, "_zephyr_integration", None)
    # executed_test_keys = getattr(session.config, "_executed_test_keys", [])

    if zephyr_enabled and integration:
        # Создаем тестовый цикл и добавляем тест-кейсы
        test_run_id = integration.create_test_cycle(test_run_name)
        print('Тестовый цикл создан: ' + str(test_run_id))
        test_case_ids = [integration.get_test_case_id(key) for key in executed_test_keys]
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
