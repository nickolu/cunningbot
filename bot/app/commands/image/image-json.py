"""
image.py
Command for generating images using OpenAI and saving them to disk.
"""

import asyncio
import json as json_module

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

    async def _image_handler(
        self, 
        interaction: discord.Interaction, 
        prompt: str, 
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        already_responded: bool = False
    ) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        await self._process_image_request(interaction, prompt, attachment, size, quality, background, already_responded, show_json=False)

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
        """Internal image handler for JSON command that displays the JSON prompt"""
        await self._process_image_request(interaction, prompt, attachment, size, quality, background, already_responded, show_json=True)

    async def _process_image_request(
        self, 
        interaction: discord.Interaction, 
        prompt: str, 
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        already_responded: bool = False,
        show_json: bool = False
    ) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        # Only defer if we haven't already responded to the interaction
        if not already_responded and not interaction.response.is_done():
            await interaction.response.defer()

        # Set defaults
        size = size or "1024x1024"
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
        
        # Format the prompt display based on whether we should show JSON
        if show_json:
            # For JSON command, show the formatted JSON
            try:
                # Try to parse and pretty-print the JSON
                parsed_json = json_module.loads(prompt)
                formatted_json = json_module.dumps(parsed_json, indent=2)
                prompt_display = f"JSON Parameters:\n```json\n{formatted_json}\n```"
            except (json_module.JSONDecodeError, TypeError):
                # Fallback to showing as regular prompt if parsing fails
                prompt_display = f"Prompt: *{prompt}*"
        else:
            prompt_display = f"Prompt: *{prompt}*"
        
        base_message_content = f"Image {action_type} for {interaction.user.mention}:\n{prompt_display}{params_text}"
        full_message_content = f"{base_message_content}{save_status_message}"

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

    @app_commands.command(name="image-json", description="Generate or edit an image with OpenAI.")
    @app_commands.describe(
        attachment="Optional: The image to edit.",
        size="Size of the generated image",
        quality="Quality of the generated image (editing only)",
        background="Background setting for the generated image (editing only)",
        json="Raw JSON string with image parameters (e.g., '{\"filter\":\"prism\",\"mood\":\"dramatic\"}')",
        subject="The main subject of the image (e.g., 'a red sports car driving down the road')",
        lighting="Lighting conditions (e.g., 'street lights at night', 'golden hour', 'studio lighting')",
        focal_length="Camera focal length (e.g., '85mm', '24mm', '200mm')",
        aperture="Camera aperture (e.g., 'f/1.4', 'f/2.8', 'f/8')",
        shutter_speed="Camera shutter speed (e.g., '1/1000', '1/60', '1s')",
        style="Photography/art style (e.g., 'sports photography', 'portrait photography', 'landscape')",
        camera="Camera model or type (e.g., 'Canon EOS R5', 'film camera', 'vintage camera')",
        lens="Lens type (e.g., 'macro lens', 'wide angle', 'telephoto')",
        iso="ISO setting (e.g. 'ISO 100', 'ISO 800', 'ISO 3200')",
        composition="Composition style (e.g., 'rule of thirds', 'center composition', 'leading lines')",
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="1024x1024 (Square)", value="1024x1024"),
            app_commands.Choice(name="1536x1024 (Landscape)", value="1536x1024"),
            app_commands.Choice(name="1024x1536 (Portrait)", value="1024x1536"),
            app_commands.Choice(name="256x256 (Small Square)", value="256x256"),
            app_commands.Choice(name="512x512 (Medium Square)", value="512x512"),
            app_commands.Choice(name="1792x1024 (Wide Landscape)", value="1792x1024"),
            app_commands.Choice(name="1024x1792 (Tall Portrait)", value="1024x1792"),
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
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None
    ) -> None:
        """Queue an image generation/editing request for processing"""
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            already_responded = False
            
            # If there are tasks in queue, inform the user
            if queue_status["queue_size"] > 0:
                action = "edit" if attachment else "generate"
                await interaction.response.send_message(
                    f"🎨 Your image {action} request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
                already_responded = True
            else:
                # If no queue, defer immediately to avoid "application did not respond"
                await interaction.response.defer()
                already_responded = True
            
            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._image_handler, 
                interaction, prompt, attachment, size, quality, background, already_responded
            )
            
            logger.info(f"Image command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing image command: {str(e)}")
            
            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."
            
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)

    @app_commands.command(name="image-json", description="Generate an image using structured parameters formatted as JSON.")
    @app_commands.describe(
        json="Raw JSON string with image parameters (e.g., '{\"filter\":\"prism\",\"mood\":\"dramatic\"}')",
        subject="The main subject of the image (e.g., 'a red sports car driving down the road')",
        lighting="Lighting conditions (e.g., 'street lights at night', 'golden hour', 'studio lighting')",
        focal_length="Camera focal length (e.g., '85mm', '24mm', '200mm')",
        aperture="Camera aperture (e.g., 'f/1.4', 'f/2.8', 'f/8')",
        shutter_speed="Camera shutter speed (e.g., '1/1000', '1/60', '1s')",
        style="Photography/art style (e.g., 'sports photography', 'portrait photography', 'landscape')",
        camera="Camera model or type (e.g., 'Canon EOS R5', 'film camera', 'vintage camera')",
        lens="Lens type (e.g., 'macro lens', 'wide angle', 'telephoto')",
        iso="ISO setting (e.g. 'ISO 100', 'ISO 800', 'ISO 3200')",
        composition="Composition style (e.g., 'rule of thirds', 'center composition', 'leading lines')",
        mood="Overall mood or atmosphere (e.g., 'dramatic', 'peaceful', 'energetic')",
        color_palette="Color scheme (e.g., 'warm tones', 'monochrome', 'vibrant colors')",
        weather="Weather conditions (e.g., 'sunny', 'stormy', 'foggy')",
        time_of_day="Time setting (e.g., 'dawn', 'midday', 'dusk', 'midnight')",
        location="Location or setting (e.g., 'urban street', 'mountain peak', 'studio')",
        # Advanced Technical Settings
        white_balance="White balance setting (e.g., 'daylight', 'tungsten', 'fluorescent', 'auto')",
        exposure_compensation="Exposure compensation (e.g., '+1 stop', '-0.5 stops', 'neutral')",
        flash="Flash setting (e.g., 'no flash', 'fill flash', 'bounce flash', 'off-camera flash')",
        focus_mode="Focus mode (e.g., 'single point', 'continuous', 'manual focus', 'zone focus')",
        metering_mode="Light metering mode (e.g., 'spot metering', 'center-weighted', 'matrix metering')",
        # Post-Processing & Effects
        contrast="Image contrast (e.g., 'high contrast', 'low contrast', 'normal', 'soft')",
        saturation="Color saturation (e.g., 'vibrant', 'desaturated', 'natural', 'oversaturated')",
        grain="Film grain or noise (e.g., 'film grain', 'digital noise', 'clean', 'heavy grain')",
        vignetting="Vignetting effect (e.g., 'natural vignette', 'heavy vignette', 'no vignette')",
        filter_type="Filter effect (e.g., 'polarizing filter', 'ND filter', 'UV filter', 'color filter')",
        processing_style="Processing style (e.g., 'natural processing', 'HDR', 'film emulation', 'digital')",
        # Creative & Artistic
        texture="Surface texture emphasis (e.g., 'smooth texture', 'rough texture', 'detailed texture')",
        depth_of_field="Depth of field control (e.g., 'shallow DOF', 'deep DOF', 'bokeh background')",
        perspective="Camera perspective (e.g., 'eye level', 'bird\'s eye', 'worm\'s eye', 'dutch angle')",
        scale="Subject scale (e.g., 'close-up', 'medium shot', 'wide shot', 'extreme close-up')",
        motion="Motion blur or freeze (e.g., 'motion blur', 'frozen action', 'panning blur', 'static')",
        # Environmental Details  
        season="Season setting (e.g., 'spring', 'summer', 'autumn', 'winter')",
        atmosphere="Atmospheric conditions (e.g., 'hazy', 'crystal clear', 'misty', 'dusty')",
        lighting_direction="Light direction (e.g., 'front lit', 'back lit', 'side lit', 'top lit')",
        shadow_quality="Shadow characteristics (e.g., 'soft shadows', 'hard shadows', 'no shadows')",
        # Subject-Specific (for people)
        pose="Subject pose (e.g., 'standing', 'sitting', 'action pose', 'candid')",
        expression="Facial expression (e.g., 'smiling', 'serious', 'contemplative', 'joyful')",
        age_range="Age appearance (e.g., 'young adult', 'middle-aged', 'elderly', 'child')",
        clothing_style="Clothing style (e.g., 'casual wear', 'formal attire', 'sports wear', 'vintage clothing')",
        # Color & Tone
        color_temperature="Color temperature (e.g., 'warm tones', 'cool tones', 'neutral', 'golden')",
        highlights="Highlight handling (e.g., 'bright highlights', 'soft highlights', 'blown highlights')",
        shadows_detail="Shadow detail (e.g., 'lifted shadows', 'crushed blacks', 'detailed shadows')",
        # Standard image generation options
        size="Size of the generated image",
        quality="Quality of the generated image",
        background="Background setting for the generated image"
    )
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Portrait Photography", value="portrait photography"),
            app_commands.Choice(name="Sports Photography", value="sports photography"),
            app_commands.Choice(name="Landscape Photography", value="landscape photography"),
            app_commands.Choice(name="Street Photography", value="street photography"),
            app_commands.Choice(name="Fashion Photography", value="fashion photography"),
            app_commands.Choice(name="Macro Photography", value="macro photography"),
            app_commands.Choice(name="Architectural Photography", value="architectural photography"),
            app_commands.Choice(name="Wildlife Photography", value="wildlife photography"),
            app_commands.Choice(name="Fine Art Photography", value="fine art photography"),
            app_commands.Choice(name="Documentary Photography", value="documentary photography"),
            app_commands.Choice(name="Cinematic", value="cinematic"),
            app_commands.Choice(name="Film Noir", value="film noir"),
            app_commands.Choice(name="Vintage", value="vintage"),
            app_commands.Choice(name="Modern", value="modern"),
        ]
    )
    @app_commands.choices(
        lighting=[
            app_commands.Choice(name="Golden Hour", value="golden hour"),
            app_commands.Choice(name="Blue Hour", value="blue hour"),
            app_commands.Choice(name="Studio Lighting", value="studio lighting"),
            app_commands.Choice(name="Natural Light", value="natural light"),
            app_commands.Choice(name="Dramatic Lighting", value="dramatic lighting"),
            app_commands.Choice(name="Soft Lighting", value="soft lighting"),
            app_commands.Choice(name="Hard Lighting", value="hard lighting"),
            app_commands.Choice(name="Backlighting", value="backlighting"),
            app_commands.Choice(name="Side Lighting", value="side lighting"),
            app_commands.Choice(name="Street Lights at Night", value="street lights at night"),
            app_commands.Choice(name="Neon Lighting", value="neon lighting"),
            app_commands.Choice(name="Candlelight", value="candlelight"),
        ]
    )
    @app_commands.choices(
        composition=[
            app_commands.Choice(name="Rule of Thirds", value="rule of thirds"),
            app_commands.Choice(name="Center Composition", value="center composition"),
            app_commands.Choice(name="Leading Lines", value="leading lines"),
            app_commands.Choice(name="Symmetry", value="symmetry"),
            app_commands.Choice(name="Framing", value="framing"),
            app_commands.Choice(name="Depth of Field", value="depth of field"),
            app_commands.Choice(name="Low Angle", value="low angle"),
            app_commands.Choice(name="High Angle", value="high angle"),
            app_commands.Choice(name="Close-up", value="close-up"),
            app_commands.Choice(name="Wide Shot", value="wide shot"),
        ]
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="1024x1024 (Square)", value="1024x1024"),
            app_commands.Choice(name="1536x1024 (Landscape)", value="1536x1024"),
            app_commands.Choice(name="1024x1536 (Portrait)", value="1024x1536"),
            app_commands.Choice(name="256x256 (Small Square)", value="256x256"),
            app_commands.Choice(name="512x512 (Medium Square)", value="512x512"),
            app_commands.Choice(name="1792x1024 (Wide Landscape)", value="1792x1024"),
            app_commands.Choice(name="1024x1792 (Tall Portrait)", value="1024x1792"),
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
    @app_commands.choices(
        white_balance=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Daylight", value="daylight"),
            app_commands.Choice(name="Tungsten", value="tungsten"),
            app_commands.Choice(name="Fluorescent", value="fluorescent"),
            app_commands.Choice(name="Cloudy", value="cloudy"),
            app_commands.Choice(name="Shade", value="shade"),
        ]
    )
    @app_commands.choices(
        flash=[
            app_commands.Choice(name="No Flash", value="no flash"),
            app_commands.Choice(name="Fill Flash", value="fill flash"),
            app_commands.Choice(name="Bounce Flash", value="bounce flash"),
            app_commands.Choice(name="Off-Camera Flash", value="off-camera flash"),
            app_commands.Choice(name="Ring Flash", value="ring flash"),
        ]
    )
    @app_commands.choices(
        processing_style=[
            app_commands.Choice(name="Natural Processing", value="natural processing"),
            app_commands.Choice(name="HDR", value="HDR"),
            app_commands.Choice(name="Film Emulation", value="film emulation"),
            app_commands.Choice(name="Digital", value="digital"),
            app_commands.Choice(name="Vintage Film", value="vintage film"),
            app_commands.Choice(name="Black and White", value="black and white"),
        ]
    )
    @app_commands.choices(
        perspective=[
            app_commands.Choice(name="Eye Level", value="eye level"),
            app_commands.Choice(name="Bird's Eye View", value="bird's eye view"),
            app_commands.Choice(name="Worm's Eye View", value="worm's eye view"),
            app_commands.Choice(name="Dutch Angle", value="dutch angle"),
            app_commands.Choice(name="Over the Shoulder", value="over the shoulder"),
        ]
    )
    @app_commands.choices(
        season=[
            app_commands.Choice(name="Spring", value="spring"),
            app_commands.Choice(name="Summer", value="summer"),
            app_commands.Choice(name="Autumn/Fall", value="autumn"),
            app_commands.Choice(name="Winter", value="winter"),
        ]
    )
    @app_commands.choices(
        motion=[
            app_commands.Choice(name="Static/Still", value="static"),
            app_commands.Choice(name="Motion Blur", value="motion blur"),
            app_commands.Choice(name="Frozen Action", value="frozen action"),
            app_commands.Choice(name="Panning Blur", value="panning blur"),
        ]
    )
    async def image_json(
        self,
        interaction: discord.Interaction,
        json: Optional[str] = None,
        subject: Optional[str] = None,
        lighting: Optional[str] = None,
        focal_length: Optional[str] = None,
        aperture: Optional[str] = None,
        shutter_speed: Optional[str] = None,
        style: Optional[str] = None,
        camera: Optional[str] = None,
        lens: Optional[str] = None,
        iso: Optional[str] = None,
        composition: Optional[str] = None,
        mood: Optional[str] = None,
        color_palette: Optional[str] = None,
        weather: Optional[str] = None,
        time_of_day: Optional[str] = None,
        location: Optional[str] = None,
        # Advanced Technical Settings
        white_balance: Optional[str] = None,
        exposure_compensation: Optional[str] = None,
        flash: Optional[str] = None,
        focus_mode: Optional[str] = None,
        metering_mode: Optional[str] = None,
        # Post-Processing & Effects
        contrast: Optional[str] = None,
        saturation: Optional[str] = None,
        grain: Optional[str] = None,
        vignetting: Optional[str] = None,
        filter_type: Optional[str] = None,
        processing_style: Optional[str] = None,
        # Creative & Artistic
        texture: Optional[str] = None,
        depth_of_field: Optional[str] = None,
        perspective: Optional[str] = None,
        scale: Optional[str] = None,
        motion: Optional[str] = None,
        # Environmental Details
        season: Optional[str] = None,
        atmosphere: Optional[str] = None,
        lighting_direction: Optional[str] = None,
        shadow_quality: Optional[str] = None,
        # Subject-Specific (for people)
        pose: Optional[str] = None,
        expression: Optional[str] = None,
        age_range: Optional[str] = None,
        clothing_style: Optional[str] = None,
        # Color & Tone
        color_temperature: Optional[str] = None,
        highlights: Optional[str] = None,
        shadows_detail: Optional[str] = None,
        # Standard image generation options
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None
    ) -> None:
        """Generate an image using structured parameters formatted as JSON"""
        
        # Parse JSON string if provided
        json_params = {}
        if json:
            try:
                json_params = json_module.loads(json)
                if not isinstance(json_params, dict):
                    await interaction.response.send_message(
                        "❌ The JSON parameter must be a valid JSON object (dictionary), not a list or primitive value.",
                        ephemeral=True
                    )
                    return
            except json_module.JSONDecodeError as e:
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
            "focalLength": focal_length,
            "aperture": aperture,
            "shutterSpeed": shutter_speed,
            "style": style,
            "camera": camera,
            "lens": lens,
            "iso": iso,
            "composition": composition,
            "mood": mood,
            "colorPalette": color_palette,
            "weather": weather,
            "timeOfDay": time_of_day,
            "location": location,
            # Advanced Technical Settings
            "whiteBalance": white_balance,
            "exposureCompensation": exposure_compensation,
            "flash": flash,
            "focusMode": focus_mode,
            "meteringMode": metering_mode,
            # Post-Processing & Effects
            "contrast": contrast,
            "saturation": saturation,
            "grain": grain,
            "vignetting": vignetting,
            "filterType": filter_type,
            "processingStyle": processing_style,
            # Creative & Artistic
            "texture": texture,
            "depthOfField": depth_of_field,
            "perspective": perspective,
            "scale": scale,
            "motion": motion,
            # Environmental Details
            "season": season,
            "atmosphere": atmosphere,
            "lightingDirection": lighting_direction,
            "shadowQuality": shadow_quality,
            # Subject-Specific
            "pose": pose,
            "expression": expression,
            "ageRange": age_range,
            "clothingStyle": clothing_style,
            # Color & Tone
            "colorTemperature": color_temperature,
            "highlights": highlights,
            "shadowsDetail": shadows_detail
        }
        
        for key, value in param_mapping.items():
            if value is not None:
                image_params[key] = value
        
        # Ensure we have at least one parameter
        if not image_params:
            await interaction.response.send_message(
                "❌ Please provide at least one parameter to generate an image. You can use the `json` parameter or any of the explicit parameters like `subject`.",
                ephemeral=True
            )
            return
        
        # Convert to JSON string
        json_prompt = json_module.dumps(image_params, indent=2)
        
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
            # We'll pass the formatted JSON string for display in the response
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
