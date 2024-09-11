

def find_folder_id_by_name(folders, folder_name):
    """Поиск папки по имени в дереве папок"""

    for folder in folders:
        if folder['name'] == folder_name:
            return folder['id']  # Возвращаем ID папки, если нашли

        # Если у папки есть дочерние элементы, продолжаем поиск в них
        if folder.get('children'):
            child_id = find_folder_id_by_name(folder['children'], folder_name)
            if child_id:
                return child_id

    return None  # Если папка с нужным именем не найдена


def get_or_create_folder(api_client, folders, folder_name):
    """Получение или создание новой папки"""

    # Дерево папок
    folder_tree = folders.get('children', [])

    # Ищем папку по имени
    folder_id = find_folder_id_by_name(folder_tree, folder_name)

    if folder_id:
        print(f"Папка '{folder_name}' найдена, ID: {folder_id}")
        return folder_id
    else:
        # Если не нашли, создаем папку в корне (без parent_id)
        print(f"Папка '{folder_name}' не найдена, создаем новую.")
        folder_id = api_client.create_test_run_folder(folder_name)
        print(f"Создана папка '{folder_name}', ID: {folder_id}")
        return folder_id
