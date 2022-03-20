#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Author:    thepoy
# @Email:     thepoy@163.com
# @File Name: __init__.py
# @Created:   2021-02-13 09:02:21
# @Modified:  2022-03-20 21:32:25

import os
import time
import json
import shutil
import requests

from io import BytesIO
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Union
from up2b.up2b_lib.custom_types import (
    ErrorResponse,
    GitGetAllImagesResponse,
    ImageBedType,
    ImageStream,
    ImageType,
    Images,
    AuthInfo,
    UploadErrorResponse,
)
from up2b.up2b_lib.utils import child_logger, check_image_exists
from up2b.up2b_lib.errors import UnsupportedType, OverSizeError
from up2b.up2b_lib.constants import (
    IMAGE_BEDS_CODE,
    CONF_FILE,
    CACHE_PATH,
)

logger = child_logger(__name__)


def choose_image_bed(image_bed_code: int, conf_file: str = CONF_FILE):
    if type(image_bed_code) != int:
        raise TypeError(
            "image bed code must be an integer, not %s" % str(type(image_bed_code))
        )
    try:
        with open(conf_file, "r+") as f:
            conf = json.loads(f.read())
            f.seek(0, 0)
            conf["image_bed"] = image_bed_code
            f.write(json.dumps(conf))
            f.truncate()
    except FileNotFoundError:
        with open(conf_file, "w") as f:
            f.write(json.dumps({"image_bed": image_bed_code}))


class ImageBedAbstract(ABC):
    @abstractmethod
    def get_all_images(self) -> List[str]:
        pass

    @abstractmethod
    def upload_image(self, image_path: str) -> Union[str, UploadErrorResponse]:
        pass

    @abstractmethod
    def upload_image_stream(
        self, image: ImageStream
    ) -> Union[str, UploadErrorResponse]:
        pass

    @abstractmethod
    def upload_images(
        self, images: Images, to_console=True
    ) -> List[Union[str, ErrorResponse]]:
        pass

    @abstractmethod
    def delete_image(
        self,
        sha: str,
        url: str,
        message: str = "Delete pictures that are no longer used",
    ) -> Optional[ErrorResponse]:
        pass

    @abstractmethod
    def delete_images(
        self,
        info: Tuple[str, str],
        message: str = "Delete pictures that are no longer used",
    ) -> Dict[str, ErrorResponse]:
        pass


class Base:
    image_bed_code: int
    max_size: int
    conf: dict
    image_bed_type: ImageBedType

    def __init__(
        self,
        auto_compress: bool = False,
        add_watermark: bool = False,
        conf_file: str = CONF_FILE,
    ):
        self.conf_file: str = conf_file
        self.auth_info: Optional[AuthInfo] = self._read_auth_info()
        self.add_watermark: bool = add_watermark
        if self.add_watermark:
            if not self.conf.get("watermark"):
                logger.fatal(
                    "you have enabled the function of adding watermark, but the watermark is not configured, please configure the text watermark through `--config-text-watermark`"
                )
        self.auto_compress: bool = auto_compress

    def check_login(self):
        if not self.auth_info:
            logger.fatal(
                "you have not logged in yet, please use the `-l` or `--login` parameter to log in"
                + " first, and the current image bed is : code=%d, name='%s'.",
                self.image_bed_code,
                self,
            )

    def _read_auth_info(self) -> Optional[AuthInfo]:
        try:
            with open(self.conf_file) as f:
                self.conf = json.loads(f.read())
                return self.conf["auth_data"][self.image_bed_code]
        except Exception:
            return None

    def _save_auth_info(self, auth_info: Dict[str, str]):
        logger.debug("current image bed code: %d", self.image_bed_code)
        try:
            with open(self.conf_file, "r+") as f:
                conf = json.loads(f.read())
                try:
                    conf["auth_data"][self.image_bed_code] = auth_info
                except KeyError:
                    conf["auth_data"] = [{}] * len(IMAGE_BEDS_CODE)
                    conf["auth_data"][self.image_bed_code] = auth_info
                f.seek(0, 0)
                f.write(json.dumps(conf))
                f.truncate()
        except FileNotFoundError:
            logger.fatal(
                "auth configure file is not found, please choose image bed with `--choose-site` or `-c` first."
            )
        except Exception as e:
            logger.fatal(e)

    def _exceed_max_size(self, *images: ImageType) -> Tuple[bool, Optional[str]]:
        for img in images:
            size = os.path.getsize(img) if isinstance(img, str) else len(img.stream)
            if size > self.max_size:
                return True, img if isinstance(img, str) else img.filename
        return False, None

    def _check_images_valid(self, *images: ImageType):
        """
        Check if all images exceed the max size or can be compressed
        """
        if not self.auto_compress:
            exceeded, _img = self._exceed_max_size(*images)
            if exceeded:
                raise OverSizeError(_img)
        else:
            for _img in images:
                mime_type = (
                    _img.split(".")[-1].lower()
                    if isinstance(_img, str)
                    else _img.mime_type
                )
                if mime_type not in ["jpg", "png", "jpeg"]:
                    raise UnsupportedType(
                        "currently does not support compression of this type of image: %s"
                        % mime_type.upper()
                    )

    def _compress_image(self, image: ImageType) -> ImageType:
        try:
            from PIL import Image
        except ModuleNotFoundError:
            logger.fatal(
                "you have enabled the automatic image compression feature, but [ pillow ] is not installed, please execute `pip install pillow` before enabling this feature"
            )

        raw_size = (
            os.path.getsize(image) if isinstance(image, str) else len(image.stream)
        )
        if raw_size > self.max_size:
            filename = os.path.basename(str(image)).split(".")[0]

            scale = self.max_size / raw_size
            img = Image.open(image if isinstance(image, str) else image.stream)

            def compress(img: Image.Image, scale: float) -> BytesIO:
                format = img.format
                width, height = img.size
                img_io = BytesIO()

                if format == "PNG":
                    img = img.convert("RGB")
                    img.format = "JPEG"  # type: ignore
                    img.save(img_io, "jpeg")
                elif format == "JPEG":
                    img = img.resize(
                        (int(width * scale), int(height * scale)), Image.ANTIALIAS
                    )
                    img.format = "JPEG"  # type: ignore
                    img.save(img_io, "jpeg")
                else:
                    raise UnsupportedType(
                        "currently does not support compression of this type of image: %s"
                        % format
                    )

                if img_io.tell() > self.max_size:
                    scale = self.max_size / img_io.tell()
                    return compress(img, scale)
                return img_io

            if img.format == "PNG":
                filename += ".jpeg"
            else:
                filename += "." + img.format.lower()  # type: ignore
            img_io = compress(img, scale)

            if not os.path.exists(CACHE_PATH):
                os.mkdir(CACHE_PATH)

            img_cache_path = os.path.join(CACHE_PATH, filename)
            with open(img_cache_path, "wb") as f:
                f.write(img_io.getbuffer())

            return img_cache_path

        return image

    def _add_watermark(self, image_path: str) -> str:
        if self.add_watermark:
            from up2b.up2b_lib.watermark import AddWatermark, TypeFont

            conf = self.conf["watermark"]
            aw = AddWatermark(conf["x"], conf["y"], conf["opacity"])
            return aw.add_text_watermark(
                image_path,
                [TypeFont(conf["text"], conf["size"], conf["font"], (0, 0, 0))],
            )
        return image_path

    def _clear_cache(self):
        if os.path.exists(CACHE_PATH):
            shutil.rmtree(CACHE_PATH)
            os.mkdir(CACHE_PATH)

            logger.info("cache folder has been cleared: %s", CACHE_PATH)


class GitBase(Base, ImageBedAbstract):
    headers: Dict[str, str]
    api_url: str

    def __init__(
        self,
        auto_compress: bool = False,
        add_watermark: bool = False,
        conf_file: str = CONF_FILE,
    ):
        super().__init__(auto_compress, add_watermark, conf_file)

        if self.auth_info:
            self.token: str = self.auth_info["token"]
            self.username: str = self.auth_info["username"]
            self.repo: str = self.auth_info["repo"]
            self.folder: str = self.auth_info["folder"]

    def login(self, token: str, username: str, repo: str, folder: str = "up2b"):
        auth_info = {
            "token": token,
            "username": username,
            "repo": repo,
            "folder": folder,
        }
        self._save_auth_info(auth_info)

    def _upload(self, image: ImageType, data: Dict[str, str], request_method="put"):
        self.check_login()

        image = self._compress_image(image)
        if isinstance(image, str):
            image = self._add_watermark(image)

        suffix = os.path.splitext(str(image))[-1]
        if suffix.lower() == ".apng":
            suffix = ".png"
        filename = f"{int(time.time() * 1000)}{suffix}"

        url = self.base_url + filename

        logger.debug("request headers: %s", self.headers)

        resp = requests.request(request_method, url, headers=self.headers, json=data)
        if resp.status_code == 201:
            uploaded_url: str = resp.json()["content"]["download_url"]
            logger.debug("uploaded: '%s' => '%s'", image, uploaded_url)
            if hasattr(self, "cdn_url") and callable(getattr(self, "cdn_url")):
                return self.cdn_url(uploaded_url)  # type: ignore

            return uploaded_url
        else:
            error = resp.json()["message"]
            logger.error("upload failed: image='%s', error='%s'", image, error)
            return UploadErrorResponse(resp.status_code, error, str(image))

    def upload_images(
        self, *images: ImageType, to_console=True
    ) -> List[Union[str, UploadErrorResponse]]:
        self.check_login()

        check_image_exists(*images)

        self._check_images_valid(*images)

        image_urls: List[Union[str, UploadErrorResponse]] = []
        for img in images:
            if isinstance(img, str):
                result = self.upload_image(img)
            else:
                result = self.upload_image_stream(img)

            image_urls.append(result)

        if hasattr(self, "cdn_url") and callable(getattr(self, "cdn_url")):
            for i in range(len(image_urls)):
                item = image_urls[i]
                if isinstance(item, str):
                    image_urls[i] = self.cdn_url(item)  # type: ignore

        if to_console:
            for i in image_urls:
                print(i)

        self._clear_cache()
        return image_urls

    @abstractmethod
    def _get_all_images_in_image_bed(
        self,
    ) -> requests.Response:
        pass

    def get_all_images(self) -> Union[List[GitGetAllImagesResponse], ErrorResponse]:
        self.check_login()

        resp = self._get_all_images_in_image_bed()
        if resp.status_code != 200:
            return ErrorResponse(resp.status_code, resp.text)

        all_images_resp: List[Dict[str, str]] = resp.json()
        images = []
        for file in all_images_resp:
            images.append(
                GitGetAllImagesResponse(
                    file["download_url"],
                    file["sha"],
                    file["url"],
                )
            )
        return images

    def _delete_image(
        self,
        sha: str,
        url: str,
        message: str = "Delete pictures that are no longer used",
        extra: Optional[Dict[str, str]] = None,
    ):
        self.check_login()

        data = {"sha": sha, "message": message}
        if extra:
            data.update(extra)

        resp = requests.delete(url, headers=self.headers, json=data)
        if resp.status_code == 200:
            return None

        return ErrorResponse(resp.status_code, resp.json()["message"])

    def delete_images(
        self,
        info: Tuple[str, str],
        message: str = "Delete pictures that are no longer used",
    ) -> Dict[str, ErrorResponse]:
        self.check_login()

        failed: Dict[str, ErrorResponse] = {}
        for sha, url in info:
            result = self.delete_image(sha, url, message)
            if result:
                failed["sha"] = result
        return failed

    @property
    def base_url(self) -> str:
        return "%s/repos/%s/%s/contents/%s/" % (
            self.api_url,
            self.username,
            self.repo,
            self.folder,
        )
