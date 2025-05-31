"""
image_edit_client.py
OpenAI image editing client for the bot, using gpt-image-1 model.
"""

import base64
import os
from openai import OpenAI
import openai # To access openai.APIError types
from typing import List, Optional, Tuple, Literal, Union, BinaryIO
from io import BytesIO

from bot.domain.logger import get_logger

logger = get_logger()

# Type definitions specific to gpt-image-1 for edits
GptImage1EditSizeType = Literal['auto', '1024x1024', '1536x1024', '1024x1536']
GptImage1QualityType = Literal['auto', 'high', 'medium', 'low']
GptImage1BackgroundType = Literal['auto', 'transparent', 'opaque']

class ImageEditClient:
    """
    Client for OpenAI's image editing API (v1/images/edits),
    specifically tailored for the 'gpt-image-1' model.
    """
    DEFAULT_MODEL = "gpt-image-1"

    def __init__(self) -> None:
        """
        Initializes the OpenAI client.
        The OpenAI API key is expected to be set in the OPENAI_API_KEY environment variable.
        """
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")
        self.client = OpenAI(api_key=self.api_key)

    def edit_image(
        self,
        image: Union[str, bytes, BinaryIO],
        prompt: str,
        mask_path: Optional[str] = None,
        n: int = 1,
        size: GptImage1EditSizeType = "1024x1024",
        quality: GptImage1QualityType = "auto",
        background: GptImage1BackgroundType = "auto",
        user: Optional[str] = None,
    ) -> Tuple[Optional[List[bytes]], str]:
        """
        Edits an image using OpenAI's gpt-image-1 model.

        Note on multiple source images for gpt-image-1:
        The OpenAI API documentation states gpt-image-1 can accept multiple source images (up to 16).
        However, the OpenAI Python library's `images.edit` method (as of late 2023/early 2024 versions)
        primarily supports a single source image file via its `image` parameter.
        This client reflects that common library usage for a single source image.
        For multi-image editing with gpt-image-1, a manual API request construction or
        a library update supporting this feature might be necessary.

        Args:
            image: Path to the source image file (PNG, JPG, WEBP, <25MB), image bytes, or a file-like object.
            prompt: A text description of the desired edits (max 32000 characters for gpt-image-1).
            mask_path: Optional path to a PNG mask file (<4MB, same dimensions as image).
                       Transparent areas indicate where the image should be edited.
            n: The number of images to generate (1-10). Defaults to 1.
            size: The size of the generated images. Defaults to '1024x1024'.
            quality: The quality of the generated image ('auto', 'high', 'medium', 'low'). Defaults to 'auto'.
            background: Background transparency setting ('auto', 'transparent', 'opaque'). Defaults to 'auto'.
                        If 'transparent', the output image format should support transparency (e.g., PNG).
            user: An optional unique identifier for the end-user, for monitoring purposes.

        Returns:
            A tuple containing:
                - A list of image bytes (one for each generated image, base64 decoded) if successful.
                - An empty string if successful, or an error message string if not.
        """
        if not (1 <= n <= 10):
            return None, "Number of images (n) must be between 1 and 10."

        try:
            image_file_to_use: BinaryIO
            opened_file: Optional[BinaryIO] = None # To ensure it's closed if we open it

            if isinstance(image, str):
                opened_file = open(image, "rb")
                image_file_to_use = opened_file
            elif isinstance(image, bytes):
                image_file_to_use = BytesIO(image)
                image_file_to_use.name = "uploaded_image.png" # OpenAI lib might need a name
            elif hasattr(image, 'read'): # Check if it's a file-like object
                image_file_to_use = image # type: ignore
            else:
                return None, "Invalid image input type. Must be path (str), bytes, or file-like object."

            # The actual API call logic will now use image_file_to_use
            # We need to ensure 'opened_file' is closed in a finally block if it was opened by this function.
            # The original 'with open(image_path, "rb") as image_file:' handled this automatically for paths.
            # Now we need a more general approach.
            try:
                mask_file_obj = None # Define here for finally block
                try:
                    api_core_params = {
                        "image": image_file_to_use,
                        "prompt": prompt,
                        "model": self.DEFAULT_MODEL,
                        "n": n,
                        "size": size,
                    }

                    extra_body_params = {}
                    if quality != "auto":
                        extra_body_params["quality"] = quality
                    if background != "auto":
                        extra_body_params["background"] = background  # type: ignore[assignment]
                    
                    if mask_path:
                        mask_file_obj = open(mask_path, "rb")
                        api_core_params["mask"] = mask_file_obj
                    
                    if user:
                        # 'user' is a standard parameter in the SDK's edit method signature
                        api_core_params["user"] = user
                    
                    if extra_body_params:
                        api_core_params["extra_body"] = extra_body_params

                    response = self.client.images.edit(**api_core_params) # type: ignore

                    if not response.data:
                        return None, "No image data returned from API."

                    image_bytes_list: List[bytes] = []
                    for img_data in response.data:
                        if img_data.b64_json:
                            image_bytes_list.append(base64.b64decode(img_data.b64_json))
                        else:
                            logger.warning(
                                f"Image data object received without b64_json content for prompt: '{prompt[:50]}...'"
                            )
                    
                    if not image_bytes_list:
                        return None, "No images with b64_json content found in response."

                    return image_bytes_list, ""
                finally:
                    if mask_file_obj and not mask_file_obj.closed:
                        mask_file_obj.close()
            finally:
                if opened_file and not opened_file.closed:
                    opened_file.close()

        except FileNotFoundError as e:
            logger.error(f"Image edit failed: File not found - {e.filename}")
            return None, f"File not found: {e.filename}"
        except openai.APIStatusError as e:
            logger.error(f"OpenAI API HTTP error during image edit: {e.status_code} - {e.response.text if e.response else 'No response text'}")
            error_detail = ""
            try:
                if e.response and e.response.content:
                    error_data = e.response.json()
                    error_detail = error_data.get("error", {}).get("message", str(e.response.json()))
                else:
                    error_detail = str(e)
            except Exception as json_ex:
                logger.warning(f"Could not parse JSON from APIStatusError response: {json_ex}")
                error_detail = e.response.text if e.response else str(e)
            
            error_message_str = f"OpenAI API Error ({e.status_code}): {error_detail if error_detail else 'See logs for details.'}"
            return None, error_message_str
        except openai.APIError as e:  # General OpenAI API errors
            logger.error(f"OpenAI API error during image edit: {e}")
            error_message_detail = ""
            if hasattr(e, 'body') and e.body and isinstance(e.body, dict) and "message" in e.body:
                error_message_detail = e.body["message"]
            elif hasattr(e, 'message') and e.message:
                error_message_detail = e.message
            
            error_message_str = f"OpenAI API Error: {error_message_detail}" if error_message_detail else f"OpenAI API Error: {str(e)}"
            return None, error_message_str
        except Exception as e:
            logger.error(f"Unexpected error during image edit: {e}")
            return None, f"An unexpected error occurred: {str(e)}"

    @staticmethod
    def factory() -> "ImageEditClient":
        """Factory method to create an instance of ImageEditClient."""
        return ImageEditClient()
