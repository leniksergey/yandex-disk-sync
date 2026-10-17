"""Сервис синхронизации локальной папки с Яндекс.Диском."""

import configparser
import logging
import os
import sys
import time

from cloud_storage import YandexDisk


def read_config(config_path: str = 'config.ini') -> dict:
    """Читает параметры из config.ini.

    :param config_path: путь к файлу конфигурации.
    :return: словарь с параметрами.
    """
    if not os.path.exists(config_path):
        sys.exit(f'Ошибка: файл конфигурации "{config_path}" не найден.')

    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding='utf-8')
        section = parser['Settings']
        token = section.get('token')
        if token in (None, '', 'YOUR_TOKEN_HERE'):
            token = os.environ.get('YANDEX_TOKEN')
        if not token:
            sys.exit(
                'Ошибка: токен не задан. Укажите token в config.ini '
                'или переменную окружения YANDEX_TOKEN.'
            )
        return {
            'local_folder': section.get('local_folder'),
            'cloud_folder': section.get('cloud_folder'),
            'token': token,
            'sync_period': section.getint('sync_period', fallback=60),
            'log_file': section.get('log_file', fallback='sync.log'),
        }
    except (configparser.Error, ValueError) as error:
        sys.exit(f'Ошибка чтения config.ini: {error}')


def setup_logger(log_file: str) -> logging.Logger:
    """Настраивает логгер с записью в файл.

    :param log_file: путь к файлу лога.
    :return: настроенный логгер.
    """
    logger = logging.getLogger('sync_service')
    logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding='utf-8-sig')
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def validate_local_folder(path: str) -> None:
    """Проверяет существование локальной папки.

    :param path: путь к синхронизируемой папке.
    """
    if not path:
        sys.exit(
            'Ошибка: путь к синхронизируемой папке не задан в config.ini.'
        )
    if not os.path.isdir(path):
        sys.exit(
            f'Ошибка: папка "{path}" не существует. '
            f'Проверьте параметр local_folder в config.ini.'
        )


def get_local_files(folder: str) -> dict:
    """Возвращает словарь {имя_файла: размер_и_время_изменения}.

    :param folder: путь к локальной папке.
    :return: словарь с сигнатурами файлов.
    """
    result = {}
    try:
        entries = os.listdir(folder)
    except OSError as error:
        logging.getLogger('sync_service').error(
            f'Не удалось прочитать папку {folder}: {error}'
        )
        return result

    for name in entries:
        full_path = os.path.join(folder, name)
        if not os.path.isfile(full_path):
            continue
        try:
            stat = os.stat(full_path)
            result[name] = (stat.st_size, stat.st_mtime)
        except OSError as error:
            logging.getLogger('sync_service').error(
                f'Не удалось получить информацию о файле {name}: {error}'
            )
    return result


def sync_files(client: YandexDisk, local_folder: str,
               previous_state: dict, logger: logging.Logger) -> dict:
    """Выполняет одну итерацию синхронизации.

    :param client: клиент облачного хранилища.
    :param local_folder: путь к локальной папке.
    :param previous_state: состояние файлов на прошлой итерации.
    :param logger: логгер.
    :return: новое состояние локальных файлов.
    """
    current_state = get_local_files(local_folder)

    try:
        cloud_files = client.get_info()
    except (ConnectionError, RuntimeError) as error:
        logger.error(f'Не удалось получить список файлов из облака: {error}')
        return previous_state if previous_state else current_state

    sync_new_files(client, local_folder, current_state, cloud_files, logger)
    sync_modified_files(
        client, local_folder, current_state, previous_state, logger
    )
    sync_deleted_files(client, current_state, cloud_files, logger)

    return current_state


def sync_new_files(client: YandexDisk, local_folder: str,
                   current_state: dict, cloud_files: dict,
                   logger: logging.Logger) -> None:
    """Загружает новые локальные файлы в облако.

    :param client: клиент облачного хранилища.
    :param local_folder: путь к локальной папке.
    :param current_state: текущее состояние файлов.
    :param cloud_files: файлы в облаке.
    :param logger: логгер.
    """
    new_names = set(current_state) - set(cloud_files)
    for name in new_names:
        full_path = os.path.join(local_folder, name)
        try:
            client.load(full_path)
            logger.info(f'Файл "{name}" успешно загружен в облако.')
        except (OSError, ConnectionError, RuntimeError) as error:
            logger.error(f'Не удалось загрузить файл "{name}": {error}')


def sync_modified_files(client: YandexDisk, local_folder: str,
                        current_state: dict, previous_state: dict,
                        logger: logging.Logger) -> None:
    """Перезаписывает изменённые локальные файлы в облаке.

    :param client: клиент облачного хранилища.
    :param local_folder: путь к локальной папке.
    :param current_state: текущее состояние файлов.
    :param previous_state: состояние на прошлой итерации.
    :param logger: логгер.
    """
    modified = []
    for name, signature in current_state.items():
        old_signature = previous_state.get(name)
        if old_signature is not None and old_signature != signature:
            modified.append(name)

    for name in modified:
        full_path = os.path.join(local_folder, name)
        try:
            client.reload(full_path)
            logger.info(f'Файл "{name}" успешно обновлён в облаке.')
        except (OSError, ConnectionError, RuntimeError) as error:
            logger.error(f'Не удалось обновить файл "{name}": {error}')


def sync_deleted_files(client: YandexDisk, current_state: dict,
                       cloud_files: dict, logger: logging.Logger) -> None:
    """Удаляет из облака файлы, удалённые локально.

    :param client: клиент облачного хранилища.
    :param current_state: текущее состояние локальных файлов.
    :param cloud_files: файлы в облаке.
    :param logger: логгер.
    """
    deleted = set(cloud_files) - set(current_state)
    for name in deleted:
        try:
            client.delete(name)
            logger.info(f'Файл "{name}" успешно удалён из облака.')
        except (ConnectionError, RuntimeError) as error:
            logger.error(f'Не удалось удалить файл "{name}": {error}')


def main() -> None:
    """Точка входа в программу."""
    config = read_config()
    logger = setup_logger(config['log_file'])

    validate_local_folder(config['local_folder'])
    logger.info('Программа запущена.')
    logger.info(f'Синхронизируемая папка: {config["local_folder"]}')

    client = YandexDisk(config['token'], config['cloud_folder'])
    if not client.check_connection():
        sys.exit(
            'Ошибка: не удалось подключиться к Яндекс.Диску. '
            'Проверьте token и cloud_folder в config.ini.'
        )

    previous_state = {}
    while True:
        previous_state = sync_files(
            client, config['local_folder'], previous_state, logger
        )
        time.sleep(config['sync_period'])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Программа остановлена пользователем.')
