"""
image_json.py
Command for generating images using structured JSON parameters with OpenAI.
"""

import asyncio
import json
import random

from typing import Optional
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.api.os.file_service import FileService
from bot.app.utils.logger import get_logger
from bot.app.task_queue import get_task_queue
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageJsonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.image_generation_client = ImageGenerationClient.factory()
        self.image_edit_client = ImageEditClient.factory()

    async def _image_json_handler(
        self, 
        interaction: discord.Interaction, 
        prompt: str, 
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        already_responded: bool = False
    ) -> None:
        """Internal image handler that processes JSON image generation requests with formatted display"""
        # Only defer if we haven't already responded to the interaction
        if not already_responded and not interaction.response.is_done():
            await interaction.response.defer()

        # Set defaults
        size = size or "auto"
        quality = quality or "auto"
        background = background or "auto"

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

            image_list_or_none, error_msg_edit = await asyncio.to_thread(
                self.image_edit_client.edit_image,
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
            generated_bytes_or_none, error_msg_gen = await self.image_generation_client.generate_image(prompt, size=size)
            final_error_message = error_msg_gen
            final_image_bytes = generated_bytes_or_none

            if not final_image_bytes:
                logger.error(f"Image operation resulted in None for final_image_bytes. Action: {action_type}, Prompt: {prompt}")
                error_msg = f"{interaction.user.mention}: An unexpected error occurred while {action_type}ing the image."
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
        if size != "1024x1024":
            params_used.append(f"Size: {size}")
        if quality != "auto":
            params_used.append(f"Quality: {quality}")
        if background != "auto":
            params_used.append(f"Background: {background}")
        
        params_text = f" ({', '.join(params_used)})" if params_used else ""
        
        # For JSON command, show the formatted JSON
        try:
            # Try to parse and pretty-print the JSON
            parsed_json = json.loads(prompt)
            formatted_json = json.dumps(parsed_json, indent=2)
            prompt_display = f"JSON Parameters:\n```json\n{formatted_json}\n```"
        except (json.JSONDecodeError, TypeError):
            # Fallback to showing as regular prompt if parsing fails
            prompt_display = f"Prompt: *{prompt}*"
        
        base_message_content = f"Image {action_type} for {interaction.user.mention}:\n{prompt_display}{params_text}"
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

    @app_commands.command(name="image-json", description="Generate an image using structured parameters formatted as JSON.")
    @app_commands.describe(
        json_string="Raw JSON string with image parameters (e.g., '{\"filter\":\"prism\",\"mood\":\"dramatic\"}')",
        subject="The main subject of the image (e.g., 'a red sports car driving down the road')",
        lighting="Lighting conditions (e.g., 'golden hour', 'studio lighting')",
        style="Photography/art style (e.g., 'portrait', 'cinematic', 'vintage')",
        focal_length="Focal length (e.g., '85mm', '24mm', '200mm')",
        aperture="Aperture (e.g., 'f/1.4', 'f/2.8', 'f/8')",
        shutter_speed="Shutter speed (e.g., '1/1000', '1/60', '1s')",
        mood="Overall mood or atmosphere (e.g., 'dramatic', 'peaceful')",
        color_palette="Color scheme (e.g., 'warm tones', 'monochrome')",
        color_temperature="Color temperature (e.g., '5000k', '6500k', '7000k')",
        weather="Weather conditions (e.g., 'sunny', 'foggy')",
        time_of_day="Time setting (e.g., 'dawn', 'dusk')",
        location="Location or setting (e.g., 'urban street', 'mountain peak')",
        size="Size of the generated image",
        quality="Quality of the generated image (e.g., 'high', 'medium', 'auto')",
        background="Background setting for the generated image (e.g., 'transparent', 'opaque', 'auto')",
        custom_1="Custom parameter name (e.g., 'colorTemperature')",
        custom_1_value="Custom parameter value (e.g., '5000k')",
        custom_2="Custom parameter name (e.g., 'colorTemperature')",
        custom_2_value="Custom parameter value (e.g., '5000k')",
        custom_3="Custom parameter name (e.g., 'colorTemperature')",
        custom_3_value="Custom parameter value (e.g., '5000k')"
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="1024x1024 (Square)", value="1024x1024"),
            app_commands.Choice(name="1536x1024 (Landscape)", value="1536x1024"),
            app_commands.Choice(name="1024x1536 (Portrait)", value="1024x1536"),
        ]
    )
    async def image_json(
        self,
        interaction: discord.Interaction,
        json_string: Optional[str] = None,
        subject: Optional[str] = None,
        lighting: Optional[str] = None,
        style: Optional[str] = None,
        focal_length: Optional[str] = None,
        aperture: Optional[str] = None,
        shutter_speed: Optional[str] = None,
        mood: Optional[str] = None,
        color_palette: Optional[str] = None,
        color_temperature: Optional[str] = None,
        weather: Optional[str] = None,
        time_of_day: Optional[str] = None,
        location: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        custom_1: Optional[str] = None,
        custom_1_value: Optional[str] = None,
        custom_2: Optional[str] = None,
        custom_2_value: Optional[str] = None,
        custom_3: Optional[str] = None,
        custom_3_value: Optional[str] = None
    ) -> None:
        """Generate an image using structured parameters formatted as JSON"""
        
        # Parse JSON string if provided
        json_params = {}
        if json_string:
            try:
                json_params = json.loads(json_string)
                if not isinstance(json_params, dict):
                    await interaction.response.send_message(
                        "❌ The JSON parameter must be a valid JSON object (dictionary), not a list or primitive value.",
                        ephemeral=True
                    )
                    return
            except json.JSONDecodeError as e:
                await interaction.response.send_message(
                    f"❌ Invalid JSON format: {str(e)}\n\n"
                    f"Example of valid JSON: `{{\"filter\":\"prism\",\"mood\":\"dramatic\"}}`",
                    ephemeral=True
                )
                return
        
        # Build the JSON object from provided parameters
        # Start with JSON params as base, then override with explicit parameters
        image_params = json_params.copy()
        
        # Add all non-None explicit parameters to the JSON object (these override JSON)
        param_mapping = {
            "subject": subject,
            "lighting": lighting,
            "style": style,
            "focal_length": focal_length,
            "aperture": aperture,
            "shutter_speed": shutter_speed,
            "mood": mood,
            "colorPalette": color_palette,
            "weather": weather,
            "timeOfDay": time_of_day,
            "location": location,
            "color_temperature": color_temperature,
            "custom_1": custom_1,
            "custom_1_value": custom_1_value,
            "custom_2": custom_2,
            "custom_2_value": custom_2_value,
            "custom_3": custom_3,
            "custom_3_value": custom_3_value,
            # Standard image generation options
            "size": size,
            "quality": quality,
            "background": background,
        }
        
        # Add non-None standard params
        for key, value in param_mapping.items():
            if key.startswith("custom_") and not key.endswith("_value"):
                if value is not None:
                    image_params[param_mapping[key]] = param_mapping[key + "_value"]
            elif key.startswith("custom_") and key.endswith("_value"):
                pass
            elif value is not None:
                image_params[key] = value
        
        # Ensure we have at least one parameter
        if not image_params:
            await interaction.response.send_message(
                "❌ Please provide at least one parameter to generate an image. You can use the `json_string` parameter or any of the explicit parameters like `subject`.",
                ephemeral=True
            )
            return
        
        # Convert to JSON string
        json_prompt = json.dumps(image_params, indent=2)
        
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            already_responded = False
            
            # If there are tasks in queue, inform the user
            if queue_status["queue_size"] > 0:
                await interaction.response.send_message(
                    f"🎨 Your structured image generation request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
                already_responded = True
            else:
                # If no queue, defer immediately to avoid "application did not respond"
                await interaction.response.defer()
                already_responded = True
            
            # Enqueue the actual image processing task with the JSON prompt
            task_id = await task_queue.enqueue_task(
                self._image_json_handler, 
                interaction, json_prompt, None, size, quality, background, already_responded
            )
            
            logger.info(f"Image-JSON command queued with task ID: {task_id}, params: {image_params}")
            
        except Exception as e:
            logger.error(f"Error queuing image-json command: {str(e)}")
            
            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."
            
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageJsonCog(bot)) 