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


@pytest.fixture(scope="session", autouse=True)
def adaptavist_integration(request):
    zephyr_enabled = request.config.getoption("--zephyr", default=False)
    test_run_name = request.config.getoption("--test_run_name", default="Test Run Cycle")

    if zephyr_enabled:
        integration = Integration()
        integration.load_environment_variables()  # Загружаем переменные только если флаг --zephyr установлен

    # if not zephyr_enabled:
    #     yield None
    #     return

        # integration = Integration(
            # api_token=JIRA_TOKEN,
            # base_url=JIRA_URL,
            # project_id=JIRA_PROJECT_ID,
            # project_name=JIRA_PROJECT_NAME,
        # )

        yield integration

        # Создаем тестовый цикл и добавляем тест-кейсы
        test_run_id = integration.create_test_cycle(test_run_name)
        print('Тестовый цикл создан: ' + test_run_id)
        test_case_ids = [integration.get_test_case_id(key) for key in executed_test_keys]
        integration.add_test_cases_to_cycle(test_run_id, test_case_ids)

        # Получаем список тестов в цикле с их ID
        # test_run_id = integration.get_test_run_id(test_run_key)
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
            # overall_status = FAIL if any(status == FAIL for status in relevant_results) else PASS

            for item in test_run_items:
                test_case_run_id = item["id"]  # id теста (в items это верхних уровень, на уровне с $lastTestResult)
                script_results = integration.get_test_script_results(test_run_id, test_case_run_id)

                parameter_set_status = {}
                for script_result in script_results[0]["testScriptResults"]:

                    # если тест параметризованный, то у него есть поле parameterSetId
                    parameter_set_id = script_result.get('parameterSetId')
                    if parameter_set_id:

                        # словарь для того, чтобы проставить единый статус у всех шагов тестового скрипта
                        if parameter_set_id not in parameter_set_status:
                            parameter_set_status[parameter_set_id] = {"status": None, "steps": []}
                        parameter_set_status[parameter_set_id]["steps"].append(script_result['id'])

                # Сортируем по parameterSetId для того, чтобы сопоставить параметризованные тесты с выполнением pytest'ом
                sorted_parameter_set_status = dict(sorted(parameter_set_status.items()))
                print('sorted_parameter_set_status')
                print(sorted_parameter_set_status)

                for param_id, info in zip(sorted_parameter_set_status.keys(), relevant_results):
                    sorted_parameter_set_status[param_id]["status"] = info

                # Формируем тело запроса для PUT /testscriptresult
                script_statuses_to_update = []
                for param_id, info in sorted_parameter_set_status.items():
                    for step_id in info["steps"]:
                        script_statuses_to_update.append({
                            "id": step_id,
                            "testResultStatusId": info["status"]
                        })

                print('script_statuses_to_update')
                print(script_statuses_to_update)

                # Установка статусов
                if script_statuses_to_update:
                    integration.set_test_script_statuses(script_statuses_to_update)
    else:
        yield None


def pytest_addoption(parser):
    """Кастомные параметры запуска автотестов."""
    parser.addoption("--zephyr", action="store_true", help="Enable Zephyr integration")
    parser.addoption("--test_run_name", action="store", default="Test Run Cycle", help="Name of the test run cycle")
