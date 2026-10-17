"""Модуль для работы с облачным хранилищем Яндекс.Диск."""

import os

import requests


class YandexDisk:
    """Класс для взаимодействия с REST API Яндекс.Диска."""

    BASE_URL = 'https://cloud-api.yandex.net/v1/disk/resources'

    def __init__(self, token: str, cloud_folder: str) -> None:
        """Инициализация клиента облачного хранилища.

        :param token: OAuth-токен доступа к Яндекс.Диску.
        :param cloud_folder: имя существующей папки в облаке.
        """
        self._token = token
        self._cloud_folder = cloud_folder.strip('/')
        self._headers = {'Authorization': f'OAuth {token}'}

    @property
    def cloud_folder(self) -> str:
        """Возвращает путь к папке в облачном хранилище."""
        return self._cloud_folder

    def _cloud_path(self, filename: str) -> str:
        """Формирует полный путь к файлу в облаке."""
        return f'{self._cloud_folder}/{filename}'

    def check_connection(self) -> bool:
        """Проверяет корректность токена и существование папки.

        :return: True, если папка доступна, иначе False.
        """
        url = self.BASE_URL
        params = {'path': self._cloud_folder}
        try:
            response = requests.get(
                url, headers=self._headers, params=params, timeout=10
            )
        except requests.RequestException:
            return False
        return response.status_code == 200

    def get_info(self) -> dict:
        """Возвращает информацию о файлах в удалённой папке.

        :return: словарь вида {имя_файла: метаданные}.
        """
        url = self.BASE_URL
        params = {'path': self._cloud_folder, 'limit': 1000}
        try:
            response = requests.get(
                url, headers=self._headers, params=params, timeout=10
            )
        except requests.RequestException as error:
            raise ConnectionError(f'Ошибка сети: {error}') from error

        if response.status_code != 200:
            raise RuntimeError(
                f'Не удалось получить список файлов: '
                f'{response.status_code} {response.text}'
            )

        items = (
            response.json()
            .get('_embedded', {})
            .get('items', [])
        )
        return {
            item['name']: item
            for item in items
            if item.get('type') == 'file'
        }

    def _get_upload_link(self, cloud_path: str, overwrite: bool) -> str:
        """Получает ссылку для загрузки файла.

        :param cloud_path: путь к файлу в облаке.
        :param overwrite: перезаписывать ли существующий файл.
        :return: URL для PUT-запроса.
        """
        url = f'{self.BASE_URL}/upload'
        params = {'path': cloud_path, 'overwrite': str(overwrite).lower()}
        try:
            response = requests.get(
                url, headers=self._headers, params=params, timeout=10
            )
        except requests.RequestException as error:
            raise ConnectionError(f'Ошибка сети: {error}') from error

        if response.status_code != 200:
            raise RuntimeError(
                f'Не удалось получить ссылку для загрузки: '
                f'{response.status_code} {response.text}'
            )
        return response.json()['href']

    def _upload(self, local_path: str, cloud_path: str,
                overwrite: bool) -> None:
        """Загружает файл по полученной ссылке.

        :param local_path: путь к локальному файлу.
        :param cloud_path: путь в облаке.
        :param overwrite: перезаписывать ли файл.
        """
        upload_url = self._get_upload_link(cloud_path, overwrite)
        try:
            with open(local_path, 'rb') as file:
                response = requests.put(upload_url, data=file, timeout=60)
        except OSError as error:
            raise OSError(f'Не удалось прочитать файл: {error}') from error
        except requests.RequestException as error:
            raise ConnectionError(
                f'Ошибка сети при загрузке: {error}'
            ) from error

        if response.status_code not in (200, 201, 202):
            raise RuntimeError(
                f'Ошибка загрузки файла: {response.status_code}'
            )

    def load(self, path: str) -> None:
        """Загружает новый файл в облачное хранилище.

        :param path: путь к локальному файлу.
        """
        filename = os.path.basename(path)
        self._upload(path, self._cloud_path(filename), overwrite=False)

    def reload(self, path: str) -> None:
        """Перезаписывает файл в облачном хранилище.

        :param path: путь к локальному файлу.
        """
        filename = os.path.basename(path)
        self._upload(path, self._cloud_path(filename), overwrite=True)

    def delete(self, filename: str) -> None:
        """Удаляет файл из облачного хранилища.

        :param filename: имя файла для удаления.
        """
        url = self.BASE_URL
        params = {'path': self._cloud_path(filename), 'permanently': 'true'}
        try:
            response = requests.delete(
                url, headers=self._headers, params=params, timeout=10
            )
        except requests.RequestException as error:
            raise ConnectionError(
                f'Ошибка сети при удалении: {error}'
            ) from error

        if response.status_code not in (200, 202, 204):
            raise RuntimeError(
                f'Не удалось удалить файл: '
                f'{response.status_code} {response.text}'
            )
