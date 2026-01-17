"""
image.py
Command for generating images using OpenAI or Google Gemini and saving them to disk.
"""

import asyncio
import random

from typing import Optional, List
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.api.google.image_generation_client import GeminiImageGenerationClient
from bot.api.google.image_edit_client import GeminiImageEditClient
from bot.api.os.file_service import FileService
from bot.app.utils.logger import get_logger
from bot.app.task_queue import get_task_queue
from bot.config import IMAGE_GENERATION_ENABLED, IMAGE_GENERATION_DISABLED_FOR_USERS
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageEditModal(discord.ui.Modal, title="Edit Images with AI"):
    """Modal for collecting prompt and size for multi-image editing"""

    prompt = discord.ui.TextInput(
        label="Prompt",
        placeholder="Describe the edit you want to make to the images...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )

    size = discord.ui.TextInput(
        label="Size (optional)",
        placeholder="auto, 1024x1024, 1536x1024, or 1024x1536",
        style=discord.TextStyle.short,
        required=False,
        default="auto",
        max_length=20
    )

    def __init__(self, attachments: List[discord.Attachment], cog: 'ImageCog'):
        super().__init__()
        self.attachments = attachments
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        """Called when the user submits the modal"""
        # Validate size
        size_value = self.size.value.strip() if self.size.value else "auto"
        valid_sizes = ["auto", "1024x1024", "1536x1024", "1024x1536"]
        if size_value not in valid_sizes:
            size_value = "auto"

        # Call the multi-image handler
        await self.cog._queue_multi_image_edit(
            interaction=interaction,
            prompt=self.prompt.value,
            attachments=self.attachments,
            size=size_value
        )

class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Initialize multiple OpenAI clients for different models
        self.openai_clients = {
            "chatgpt-image-latest": ImageGenerationClient.factory(model="chatgpt-image-latest"),
            "gpt-image-1.5": ImageGenerationClient.factory(model="gpt-image-1.5"),
            "gpt-image-1": ImageGenerationClient.factory(model="gpt-image-1"),
            "gpt-image-1-mini": ImageGenerationClient.factory(model="gpt-image-1-mini"),
        }
        # Default OpenAI client for backward compatibility
        self.openai_generation_client = self.openai_clients["gpt-image-1"]
        self.openai_edit_client = ImageEditClient.factory()

        # Initialize Gemini clients (will only work if GOOGLE_API_KEY is set)
        self.gemini_generation_clients = {}
        self.gemini_edit_client = None
        try:
            self.gemini_generation_clients = {
                "gemini": GeminiImageGenerationClient.factory(model="gemini-2.5-flash-image"),
                "gemini-3-pro-image-preview": GeminiImageGenerationClient.factory(model="gemini-3-pro-image-preview"),
            }
            # Default Gemini client for backward compatibility
            self.gemini_generation_client = self.gemini_generation_clients["gemini"]
            self.gemini_edit_client = GeminiImageEditClient.factory()
        except EnvironmentError as e:
            logger.warning(f"Gemini image generation unavailable: {e}")
            self.gemini_generation_client = None

    async def _image_handler(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        model: Optional[str] = None,
        already_responded: bool = False
    ) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        try:
            # Only defer if we haven't already responded to the interaction
            if not already_responded and not interaction.response.is_done():
                await interaction.response.defer()

            # Set defaults
            size = size or "auto"
            quality = quality or "auto"
            background = background or "auto"
            # Default to Gemini if available; otherwise gpt-image-1. Respect explicit user choice.
            if model is None:
                model = "gemini" if self.gemini_generation_client else "gpt-image-1"

            # Validate explicit Gemini selection (show error), but if defaulting and unavailable we already fell back
            is_gemini_model = model in self.gemini_generation_clients or model == "gemini"
            if is_gemini_model and not self.gemini_generation_client:
                error_msg = "Google Gemini model is not available. Please ensure GOOGLE_API_KEY is configured."
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg)
                else:
                    await interaction.response.send_message(error_msg)
                return

            # Select the appropriate clients based on model
            if model in self.gemini_generation_clients:
                # Use the specific Gemini model client
                generation_client = self.gemini_generation_clients[model]
                edit_client = self.gemini_edit_client
            elif model in self.openai_clients:
                # Use the specific OpenAI model client
                generation_client = self.openai_clients[model]
                edit_client = self.openai_edit_client
            else:
                # Fallback for backward compatibility (e.g., "openai")
                generation_client = self.openai_generation_client
                edit_client = self.openai_edit_client

            final_image_bytes: Optional[bytes] = None
            final_error_message: str = ""
            filename: str = ""
            filepath: str = ""
            action_type: str = ""

            if attachment:
                print(f"Editing image: {attachment.filename}...")
                action_type = "edited"
                try:
                    image_to_edit_bytes = await attachment.read()
                except Exception as e:
                    logger.error(f"Failed to read attachment: {e}")
                    error_msg = f"{interaction.user.mention}: Failed to read the attached image.\n\nError: {e}"
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return

                # For Gemini, check if editing is supported
                if model in self.gemini_generation_clients or model == "gemini":
                    # Gemini supports editing
                    image_list_or_none, error_msg_edit = await edit_client.edit_image(
                        image=image_to_edit_bytes,
                        prompt=prompt,
                        size=size
                    )
                else:
                    # OpenAI editing
                    image_list_or_none, error_msg_edit = await asyncio.to_thread(
                        edit_client.edit_image,
                        image=image_to_edit_bytes,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        background=background
                    )
                final_error_message = error_msg_edit
                if image_list_or_none and len(image_list_or_none) > 0:
                    final_image_bytes = image_list_or_none[0] # Use the first image
                
                if not final_image_bytes:
                    # Ensure there's an error message if no image was produced
                    if not final_error_message: final_error_message = "Image editing resulted in no image data."
                    
                    # Check if it's a rate limit error from Gemini
                    if (
                        final_error_message.startswith("RATE_LIMIT:")
                        or "429" in final_error_message
                        or "resource_exhausted" in final_error_message.lower()
                        or "quota" in final_error_message.lower()
                        or final_error_message.strip() == "'error'"
                    ):
                        rate_limit_msg = final_error_message.replace("RATE_LIMIT: ", "")
                        error_msg = (
                            f"⏱️ **Rate Limit Reached**\n\n"
                            f"{interaction.user.mention}, {rate_limit_msg}\n\n"
                            f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                            f"Your request is valid and will work once the rate limit clears.*\n\n"
                            f"**Your request:**\n"
                            f"• Prompt: *{prompt}*\n"
                            f"• Model: {model.upper()}\n"
                            f"• Attachment: *{attachment.filename}*"
                        )
                    else:
                        error_msg = f"{interaction.user.mention}: Image editing failed\n\nprompt: *{prompt}*\nattachment: *{attachment.filename}*\n\n{final_error_message}"
                    
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return
                
                filename = f"edited_{attachment.filename}_{uuid.uuid4().hex[:8]}.png"
                filepath = f"edited_images/{interaction.user.display_name}/{filename}"

            else:
                print("Generating image...")
                action_type = "generated"
                generated_bytes_or_none, error_msg_gen = await generation_client.generate_image(prompt, size=size)
                final_error_message = error_msg_gen
                final_image_bytes = generated_bytes_or_none

                if not final_image_bytes:
                    logger.error(f"Image operation resulted in None for final_image_bytes. Action: {action_type}, Prompt: {prompt}, Error: {final_error_message}")
                    
                    # Check if it's a rate limit error from Gemini
                    if (
                        final_error_message
                        and (
                            final_error_message.startswith("RATE_LIMIT:")
                            or "429" in final_error_message
                            or ("resource_exhausted" in final_error_message.lower())
                            or ("quota" in final_error_message.lower())
                            or (final_error_message.strip() == "'error'")
                        )
                    ):
                        rate_limit_msg = final_error_message.replace("RATE_LIMIT: ", "")
                        error_msg = (
                            f"⏱️ **Rate Limit Reached**\n\n"
                            f"{interaction.user.mention}, {rate_limit_msg}\n\n"
                            f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                            f"Your request is valid and will work once the rate limit clears.*\n\n"
                            f"**Your request:**\n"
                            f"• Prompt: *{prompt}*\n"
                            f"• Model: {model.upper()}\n"
                            f"• Size: {size}"
                        )
                    else:
                        error_msg = f"{interaction.user.mention}: An unexpected error occurred while {action_type}ing the image."
                        if final_error_message:
                            error_msg += f"\n\n{final_error_message}"
                    
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return
            
                filename = f"generated_{uuid.uuid4().hex[:8]}.png"
                # Using relative paths, ensure the base directory ('generated_images', 'edited_images')
                # is writable by the application user in the Docker container.
                base_dir = "generated_images"
                filepath = f"{base_dir}/{interaction.user.display_name}/{filename}"

            discord_file_attachment = None
            # Prepare BytesIO object for Discord message from final_image_bytes.
            # This is done before attempting to save, so it's available even if saving fails.
            image_stream = BytesIO(final_image_bytes)
            image_stream.seek(0)
            discord_file_attachment = discord.File(fp=image_stream, filename=filename)

            save_status_message = ""
            try:
                FileService.write_bytes(filepath, final_image_bytes)
                logger.info(f"Image {action_type} and saved: {filepath}")
                # Positive confirmation of saving can be part of the message if desired.
                # For example: save_status_message = f"\nImage saved as `{filepath}`."
            except Exception as e:
                # This 'e' will be the PermissionError from the traceback in your case.
                logger.error(f"Failed to save image to {filepath}: {e}", exc_info=True) # Log with traceback
                error_type = type(e).__name__
                save_status_message = f"\n\n**Warning:** Failed to save image to disk ({error_type}: {e}). The image is still attached to this message."

            # Build message with parameters used
            params_used = []
            params_used.append(f"Model: {model.upper()}")
            if size != "1024x1024" and size != "auto":
                params_used.append(f"Size: {size}")
            # Check if it's an OpenAI model (not Gemini)
            is_gemini = model in self.gemini_generation_clients or model == "gemini"
            is_openai_model = not is_gemini
            if quality != "auto" and is_openai_model:
                params_used.append(f"Quality: {quality}")
            if background != "auto" and is_openai_model:
                params_used.append(f"Background: {background}")

            params_text = f" ({', '.join(params_used)})" if params_used else ""
            base_message_content = f"Image {action_type} for {interaction.user.mention}:\nPrompt: *{prompt}*{params_text}"
            full_message_content = f"{base_message_content}{save_status_message}"
            
            # Check if we should send donation message (1/20 chance)
            random_number = random.randint(1, 20)
            should_send_donation = random_number == 1
            logger.info(f"Should send donation: {should_send_donation}, random number: {random_number}")

            # Send the result using the appropriate method
            if interaction.response.is_done():
                await interaction.followup.send(
                    content=full_message_content,
                    file=discord_file_attachment
                )
            else:
                await interaction.response.send_message(
                    content=full_message_content,
                    file=discord_file_attachment
                )
            
            # Send donation message as follow-up if selected
            if should_send_donation:
                donation_message = f"Hey {interaction.user.mention}, if you're getting value out of this bot, consider suporting it by donating! (Each image generation costs about $0.25)\n Donate here: https://www.paypal.com/donate/?hosted_button_id=MV6C7HNDU45EU \n\n (this message is sent randomly about 5% of the time)"
                await interaction.followup.send(donation_message, ephemeral=True)
                logger.info(f"Donation message sent to {interaction.user.mention}")
                
        except Exception as e:
            # Catch any unexpected errors and send a friendly message
            error_str = str(e)
            logger.error(f"Unexpected error in _image_handler: {e}", exc_info=True)
            
            # Check if it's a rate limit error
            is_gemini = model in self.gemini_generation_clients or model == "gemini"
            if (
                "RATE_LIMIT:" in error_str
                or "429" in error_str
                or "Too Many Requests" in error_str
                or error_str.strip() == "'error'"  # Gemini SDK sometimes yields a bare 'error'
                or is_gemini  # Force friendly message for Gemini on unexpected errors
            ):
                error_msg = (
                    f"⏱️ **Rate Limit Reached**\n\n"
                    f"{interaction.user.mention}, Google Gemini is currently experiencing high demand. Please try again in a few moments.\n\n"
                    f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                    f"Your request is valid and will work once the rate limit clears.*\n\n"
                    f"**Your request:**\n"
                    f"• Prompt: *{prompt}*\n"
                    f"• Model: {model if model else 'OPENAI'}\n"
                    f"• Size: {size if size else 'auto'}"
                )
            else:
                # Generic error message
                error_msg = (
                    f"{interaction.user.mention}, an unexpected error occurred while processing your image request.\n\n"
                    f"**Error:** {error_str}\n\n"
                    f"Please try again or contact support if this persists."
                )
            
            # Send error message
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)

    async def _queue_multi_image_edit(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachments: List[discord.Attachment],
        size: str
    ) -> None:
        """Queue a multi-image editing request for processing"""
        try:
            # Defer the interaction response
            await interaction.response.defer()
            already_responded = True

            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()

            # If there are tasks in queue, inform the user via followup
            if queue_status["queue_size"] > 0:
                await interaction.followup.send(
                    f"🎨 Your multi-image edit request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your images as soon as I finish the current requests.",
                    ephemeral=True
                )

            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._multi_image_handler,
                interaction, prompt, attachments, size, already_responded
            )

            logger.info(f"Multi-image edit command queued with task ID: {task_id}")

        except Exception as e:
            logger.error(f"Error queuing multi-image edit command: {str(e)}")

            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."

            # We've already deferred, so use followup
            await interaction.followup.send(error_message, ephemeral=True)

    async def _multi_image_handler(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachments: List[discord.Attachment],
        size: str,
        already_responded: bool = False
    ) -> None:
        """Internal handler for multi-image editing requests"""
        try:
            # Only defer if we haven't already responded to the interaction
            if not already_responded and not interaction.response.is_done():
                await interaction.response.defer()

            # Hard-code model to gemini-3-pro-image-preview
            model = "gemini-3-pro-image-preview"

            # Check if Gemini 3 Pro is available
            if model not in self.gemini_generation_clients or not self.gemini_edit_client:
                error_msg = "❌ Google Gemini 3 Pro is not available. Please ensure GOOGLE_API_KEY is configured."
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg)
                else:
                    await interaction.response.send_message(error_msg)
                return

            edit_client = self.gemini_edit_client

            # Download all images in parallel
            async def download_attachment(att: discord.Attachment) -> tuple[str, Optional[bytes]]:
                """Download a single attachment and return (filename, bytes or None)"""
                try:
                    return att.filename, await att.read()
                except Exception as e:
                    logger.error(f"Failed to download {att.filename}: {e}")
                    return att.filename, None

            download_results = await asyncio.gather(
                *[download_attachment(att) for att in attachments]
            )

            # Separate successful downloads from failures
            failed_downloads = [filename for filename, data in download_results if data is None]
            successful_downloads = [(filename, data) for filename, data in download_results if data is not None]

            if failed_downloads:
                logger.warning(f"Failed to download {len(failed_downloads)} images: {failed_downloads}")

            if not successful_downloads:
                error_msg = (
                    f"{interaction.user.mention}: Failed to download any of the attached images.\n\n"
                    f"Failed files: {', '.join(failed_downloads)}"
                )
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg)
                else:
                    await interaction.response.send_message(error_msg)
                return

            # Prepare image input as List[bytes]
            image_bytes_list = [data for _, data in successful_downloads]
            filenames = [filename for filename, _ in successful_downloads]

            logger.info(f"Processing {len(image_bytes_list)} images: {filenames}")

            # Call Gemini edit_image with list of images
            result_images, error_msg_edit = await edit_client.edit_image(
                image=image_bytes_list,
                prompt=prompt,
                size=size
            )

            if not result_images or len(result_images) == 0:
                # Handle errors
                if not error_msg_edit:
                    error_msg_edit = "Image editing resulted in no image data."

                # Check if it's a rate limit error
                if (
                    error_msg_edit.startswith("RATE_LIMIT:")
                    or "429" in error_msg_edit
                    or "resource_exhausted" in error_msg_edit.lower()
                    or "quota" in error_msg_edit.lower()
                    or error_msg_edit.strip() == "'error'"
                ):
                    rate_limit_msg = error_msg_edit.replace("RATE_LIMIT: ", "")
                    error_msg = (
                        f"⏱️ **Rate Limit Reached**\n\n"
                        f"{interaction.user.mention}, {rate_limit_msg}\n\n"
                        f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                        f"Your request is valid and will work once the rate limit clears.*\n\n"
                        f"**Your request:**\n"
                        f"• Prompt: *{prompt}*\n"
                        f"• Model: GEMINI-3-PRO-IMAGE-PREVIEW\n"
                        f"• Images: {len(image_bytes_list)}"
                    )
                else:
                    error_msg = (
                        f"{interaction.user.mention}: Multi-image editing failed\n\n"
                        f"Prompt: *{prompt}*\n"
                        f"Images: {', '.join(filenames)}\n\n"
                        f"{error_msg_edit}"
                    )

                if interaction.response.is_done():
                    await interaction.followup.send(error_msg)
                else:
                    await interaction.response.send_message(error_msg)
                return

            # Use the first generated image
            final_image_bytes = result_images[0]
            filename = f"multi_edit_{uuid.uuid4().hex[:8]}.png"
            filepath = f"edited_images/{interaction.user.display_name}/{filename}"

            # Prepare Discord file attachment
            image_stream = BytesIO(final_image_bytes)
            image_stream.seek(0)
            discord_file_attachment = discord.File(fp=image_stream, filename=filename)

            # Try to save to disk (graceful failure)
            save_status_message = ""
            try:
                FileService.write_bytes(filepath, final_image_bytes)
                logger.info(f"Multi-image edited and saved: {filepath}")
            except Exception as e:
                logger.error(f"Failed to save image to {filepath}: {e}", exc_info=True)
                error_type = type(e).__name__
                save_status_message = f"\n\n**Warning:** Failed to save image to disk ({error_type}: {e}). The image is still attached to this message."

            # Build result message
            success_count = len(image_bytes_list)
            fail_count = len(failed_downloads)
            images_text = f"{success_count} image{'s' if success_count != 1 else ''}"
            if fail_count > 0:
                images_text += f" ({fail_count} failed to download)"

            base_message_content = (
                f"Multi-image edit completed for {interaction.user.mention}:\n"
                f"Prompt: *{prompt}*\n"
                f"Model: GEMINI-3-PRO-IMAGE-PREVIEW\n"
                f"Images processed: {images_text}"
            )
            full_message_content = f"{base_message_content}{save_status_message}"

            # Send the result
            if interaction.response.is_done():
                await interaction.followup.send(
                    content=full_message_content,
                    file=discord_file_attachment
                )
            else:
                await interaction.response.send_message(
                    content=full_message_content,
                    file=discord_file_attachment
                )

        except Exception as e:
            # Catch any unexpected errors and send a friendly message
            error_str = str(e)
            logger.error(f"Unexpected error in _multi_image_handler: {e}", exc_info=True)

            # Check if it's a rate limit error
            if (
                "RATE_LIMIT:" in error_str
                or "429" in error_str
                or "Too Many Requests" in error_str
                or error_str.strip() == "'error'"
            ):
                error_msg = (
                    f"⏱️ **Rate Limit Reached**\n\n"
                    f"{interaction.user.mention}, Google Gemini is currently experiencing high demand. Please try again in a few moments.\n\n"
                    f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                    f"Your request is valid and will work once the rate limit clears.*\n\n"
                    f"**Your request:**\n"
                    f"• Prompt: *{prompt}*\n"
                    f"• Model: GEMINI-3-PRO-IMAGE-PREVIEW"
                )
            else:
                # Generic error message
                error_msg = (
                    f"{interaction.user.mention}, an unexpected error occurred while processing your multi-image request.\n\n"
                    f"**Error:** {error_str}\n\n"
                    f"Please try again or contact support if this persists."
                )

            # Send error message
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)

    @app_commands.command(name="image", description="Generate or edit an image with OpenAI or Google Gemini.")
    @app_commands.describe(
        prompt="Describe the image you want to generate or the edit you want to make.",
        attachment="Optional: The image to edit.",
        model="AI model to use for generation",
        size="Size of the generated image",
        quality="Quality of the generated image (OpenAI only, editing only)",
        background="Background setting for the generated image (OpenAI only, editing only)"
    )
    @app_commands.choices(
        model=[
            app_commands.Choice(name="Google Gemini 2.5 Flash", value="gemini"),
            app_commands.Choice(name="Google Gemini 3 Pro", value="gemini-3-pro-image-preview"),
            app_commands.Choice(name="ChatGPT Image (Latest)", value="chatgpt-image-latest"),
            app_commands.Choice(name="GPT Image 1.5", value="gpt-image-1.5"),
            app_commands.Choice(name="GPT Image 1", value="gpt-image-1"),
            app_commands.Choice(name="GPT Image 1 Mini", value="gpt-image-1-mini"),
        ]
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="1024x1024 (Square)", value="1024x1024"),
            app_commands.Choice(name="1536x1024 (Landscape)", value="1536x1024"),
            app_commands.Choice(name="1024x1536 (Portrait)", value="1024x1536"),
        ]
    )
    @app_commands.choices(
        quality=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Low", value="low"),
        ]
    )
    @app_commands.choices(
        background=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Transparent", value="transparent"),
            app_commands.Choice(name="Opaque", value="opaque"),
        ]
    )
    async def image(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachment: Optional[discord.Attachment] = None,
        model: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None
    ) -> None:
        """Queue an image generation/editing request for processing"""
        # Check if image generation is globally enabled
        if not IMAGE_GENERATION_ENABLED:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return
            
        # Check if user is in the disabled list
        if str(interaction.user.id) in IMAGE_GENERATION_DISABLED_FOR_USERS:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return
        
        # CRITICAL: Defer immediately to avoid Discord's 3-second timeout
        # We must respond to the interaction before doing any other logic
        await interaction.response.defer()
        already_responded = True
            
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            # If there are tasks in queue, inform the user via followup
            if queue_status["queue_size"] > 0:
                action = "edit" if attachment else "generate"
                await interaction.followup.send(
                    f"🎨 Your image {action} request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
            
            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._image_handler,
                interaction, prompt, attachment, size, quality, background, model, already_responded
            )
            
            logger.info(f"Image command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing image command: {str(e)}")
            
            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."
            
            # We've already deferred, so use followup
            await interaction.followup.send(error_message, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    cog = ImageCog(bot)
    await bot.add_cog(cog)

    # Define context menu command at module level (required by discord.py)
    @app_commands.context_menu(name="Edit Images")
    async def edit_images_context_menu(
        interaction: discord.Interaction,
        message: discord.Message
    ) -> None:
        """Context menu command to edit all images in a message"""
        # Check if image generation is globally enabled
        if not IMAGE_GENERATION_ENABLED:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Check if user is in the disabled list
        if str(interaction.user.id) in IMAGE_GENERATION_DISABLED_FOR_USERS:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Check if Gemini 3 Pro is available
        if "gemini-3-pro-image-preview" not in cog.gemini_generation_clients or not cog.gemini_edit_client:
            error_message = "❌ Google Gemini is not available. Please ensure GOOGLE_API_KEY is configured."
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Extract all image attachments from the message
        image_attachments = [
            att for att in message.attachments
            if att.content_type and att.content_type.startswith('image/')
        ]

        # Validate at least one image exists
        if not image_attachments:
            error_message = "❌ This message doesn't contain any images to edit."
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Validate file sizes (reject >25MB)
        MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB in bytes
        oversized_files = [
            att.filename for att in image_attachments
            if att.size > MAX_FILE_SIZE
        ]

        if oversized_files:
            error_message = f"❌ Some images are too large (>25MB): {', '.join(oversized_files)}"
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Validate count (max 10 images)
        MAX_IMAGES = 10
        if len(image_attachments) > MAX_IMAGES:
            error_message = f"❌ Too many images ({len(image_attachments)}). Maximum is {MAX_IMAGES} images per edit."
            await interaction.response.send_message(error_message, ephemeral=True)
            return

        # Show modal to get prompt and size
        modal = ImageEditModal(attachments=image_attachments, cog=cog)
        await interaction.response.send_modal(modal)

    # Add the context menu to the bot's command tree
    bot.tree.add_command(edit_images_context_menu)
