# Task Queue System Usage Guide

## Overview

The task queue system has been implemented to handle multiple requests concurrently by queuing them and processing them sequentially. This prevents the bot from being overwhelmed and ensures a better user experience by managing resource usage effectively.

## How It Works

### Core Components

1. **TaskQueue**: Manages the queue of tasks and processes them one at a time
2. **Task**: Represents a single task with metadata like status, creation time, and error handling
3. **Worker**: Background process that continuously processes tasks from the queue

### Task Flow

1. User sends a command (e.g., `/chat` or `/image`)
2. Command is immediately queued instead of being processed directly  
3. User receives feedback about queue status if there are pending tasks
4. Background worker processes tasks sequentially (first-in-first-out)
5. Results are sent back to the user when processing completes

## Features

### Queue Management
- **Maximum Queue Size**: 100 tasks (configurable)
- **Task History**: Keeps track of the last 50 completed tasks
- **Automatic Worker Management**: Starts/stops background processing as needed
- **Graceful Shutdown**: Properly stops the worker when bot shuts down

### Error Handling
- **Interaction Expiration**: Cancels tasks if Discord interactions expire
- **Task Failure Recovery**: Captures errors and notifies users appropriately
- **Queue Full Protection**: Rejects new tasks when queue is at capacity

### User Feedback
- **Queue Position**: Users are informed if there are tasks ahead of them
- **Processing Status**: Visual indicators (🕐, 🎨) for different command types
- **Error Messages**: Clear communication when things go wrong

## Commands with Queue Support

### Currently Implemented
- `/chat` - Chat with the AI (queued for sequential processing)
- `/image` - Generate or edit images (queued due to processing time)
- `/queue` - Check current queue status

### Usage Examples

```
/chat msg:"Hello, how are you?" 
# If queue has tasks: "🕐 Your request has been queued! There are 2 tasks ahead of you..."

/image prompt:"A sunset over the mountains"
# If queue has tasks: "🎨 Your image generate request has been queued! There are 1 tasks ahead of you..."

/queue
# Shows: Queue Statistics, Worker Status, Current queue size
```

## For Developers

### Adding Queue Support to New Commands

To add queue support to a new command, follow this pattern:

1. **Create the internal handler method:**
```python
async def _your_command_handler(self, interaction: discord.Interaction, ...args) -> None:
    """Internal handler that does the actual work"""
    await interaction.response.defer()
    # Your existing command logic here
    # ...
```

2. **Update the public command to use queuing:**
```python
from bot.core.task_queue import get_task_queue

@app_commands.command(name="yourcommand", description="Your command description")
async def your_command(self, interaction: discord.Interaction, ...args) -> None:
    """Queue your command for processing"""
    try:
        task_queue = get_task_queue()
        queue_status = task_queue.get_queue_status()
        
        # Inform user if there are queued tasks
        if queue_status["queue_size"] > 0:
            await interaction.response.send_message(
                f"⏳ Your request has been queued! There are {queue_status['queue_size']} tasks ahead of you.",
                ephemeral=True
            )
        
        # Enqueue the task
        task_id = await task_queue.enqueue_task(
            self._your_command_handler, 
            interaction, ...args
        )
        
        logger.info(f"Command queued with task ID: {task_id}")
        
    except Exception as e:
        logger.error(f"Error queuing command: {str(e)}")
        await interaction.response.send_message(
            "Sorry, I'm currently overwhelmed. Please try again later.",
            ephemeral=True
        )
```

### Using the @queued_task Decorator (Alternative)

For simpler cases, you can use the decorator approach:

```python
from bot.core.task_queue import queued_task

@queued_task
async def your_command_handler(interaction: discord.Interaction, ...args):
    await interaction.response.defer()
    # Your command logic here
```

### Queue Configuration

The task queue can be configured in `bot/core/task_queue.py`:

- `max_queue_size`: Maximum number of tasks that can be queued (default: 100)
- `max_completed_history`: Number of completed tasks to keep in memory (default: 50)

### Monitoring and Debugging

Use the `/queue` command to monitor:
- Current queue size
- Number of active tasks
- Number of completed tasks  
- Worker status (running/stopped)

Check logs for detailed task processing information:
```
Task queue worker started
Task task_123456789_987654321_1 enqueued. Queue size: 1
Processing task task_123456789_987654321_1  
Task task_123456789_987654321_1 completed successfully
```

## Best Practices

1. **Use queuing for resource-intensive commands** (AI calls, image processing, etc.)
2. **Provide clear user feedback** about queue status and estimated wait times
3. **Handle errors gracefully** and inform users appropriately
4. **Monitor queue size** and adjust `max_queue_size` if needed
5. **Test interaction expiration** handling for long-running tasks

## Troubleshooting

### Common Issues

**Queue Full Error:**
- Increase `max_queue_size` or optimize task processing speed
- Check for stuck tasks that aren't completing

**Worker Not Starting:**
- Check logs for initialization errors
- Ensure task queue is properly initialized in `main.py`

**Interaction Expired:**
- Tasks taking longer than 15 minutes will see expired interactions
- Consider breaking long tasks into smaller chunks

**Memory Usage:**
- Monitor `completed_tasks` size if bot runs for extended periods
- Adjust `max_completed_history` if needed

### Debug Commands

The system provides built-in monitoring through the `/queue` command, but you can also add custom debugging by accessing the task queue directly:

```python
from bot.core.task_queue import get_task_queue

task_queue = get_task_queue()
status = task_queue.get_queue_status()
print(f"Queue size: {status['queue_size']}")
``` 