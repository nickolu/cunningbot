"""
Task Queue System for CunningBot
Handles queuing and sequential processing of bot tasks to prevent concurrent execution.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import discord
from enum import Enum

logger = logging.getLogger("CunningBot.TaskQueue")

class TaskStatus(Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

@dataclass
class Task:
    """Represents a task in the queue"""
    task_id: str
    handler: Callable
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    created_at: datetime = field(default_factory=datetime.now)
    status: TaskStatus = TaskStatus.QUEUED
    result: Optional[Any] = None
    error: Optional[str] = None
    interaction: Optional[discord.Interaction] = None

    def __post_init__(self):
        # Extract interaction from args if it's a Discord command
        if self.args and isinstance(self.args[0], discord.Interaction):
            self.interaction = self.args[0]
        elif 'interaction' in self.kwargs:
            self.interaction = self.kwargs['interaction']

class TaskQueue:
    """Manages a queue of tasks and processes them sequentially"""
    
    def __init__(self, max_queue_size: int = 100):
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self.active_tasks: Dict[str, Task] = {}
        self.completed_tasks: Dict[str, Task] = {}
        self.max_completed_history = 50
        self.is_processing = False
        self._task_counter = 0
        self._worker_task: Optional[asyncio.Task] = None
        
    async def start_worker(self):
        """Start the background worker that processes tasks"""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            logger.info("Task queue worker started")
    
    async def stop_worker(self):
        """Stop the background worker"""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            logger.info("Task queue worker stopped")
    
    def generate_task_id(self, interaction: Optional[discord.Interaction] = None) -> str:
        """Generate a unique task ID"""
        self._task_counter += 1
        if interaction:
            return f"task_{interaction.user.id}_{interaction.id}_{self._task_counter}"
        return f"task_{self._task_counter}"
    
    async def enqueue_task(self, handler: Callable, *args, **kwargs) -> str:
        """Add a task to the queue"""
        task_id = self.generate_task_id()
        
        # Extract interaction if present
        interaction = None
        if args and isinstance(args[0], discord.Interaction):
            interaction = args[0]
        elif 'interaction' in kwargs:
            interaction = kwargs['interaction']
            
        task = Task(
            task_id=task_id,
            handler=handler,
            args=args,
            kwargs=kwargs,
            interaction=interaction
        )
        
        try:
            await self.queue.put(task)
            self.active_tasks[task_id] = task
            logger.info(f"Task {task_id} enqueued. Queue size: {self.queue.qsize()}")
            
            # Start worker if not already running
            await self.start_worker()
            
            return task_id
        except asyncio.QueueFull:
            logger.error(f"Task queue is full. Cannot enqueue task {task_id}")
            raise Exception("Task queue is full. Please try again later.")
    
    async def _worker(self):
        """Background worker that processes tasks sequentially"""
        logger.info("Task queue worker started processing")
        
        while True:
            try:
                # Get next task from queue
                task = await self.queue.get()
                
                if task.task_id in self.active_tasks:
                    await self._process_task(task)
                    self.queue.task_done()
                else:
                    logger.warning(f"Task {task.task_id} not found in active tasks")
                    
            except asyncio.CancelledError:
                logger.info("Task queue worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in task queue worker: {e}")
                await asyncio.sleep(1)  # Brief pause before continuing
    
    async def _process_task(self, task: Task):
        """Process a single task"""
        logger.info(f"Processing task {task.task_id}")
        task.status = TaskStatus.PROCESSING
        
        try:
            # If there's an interaction, check if it's still valid
            if task.interaction and task.interaction.is_expired():
                logger.warning(f"Task {task.task_id} interaction has expired")
                task.status = TaskStatus.CANCELLED
                task.error = "Interaction expired"
                return
            
            # Execute the task
            if asyncio.iscoroutinefunction(task.handler):
                result = await task.handler(*task.args, **task.kwargs)
            else:
                result = task.handler(*task.args, **task.kwargs)
            
            task.result = result
            task.status = TaskStatus.COMPLETED
            logger.info(f"Task {task.task_id} completed successfully")
            
        except Exception as e:
            task.error = str(e)
            task.status = TaskStatus.FAILED
            logger.error(f"Task {task.task_id} failed: {e}")
            
            # Try to send error message to user if it's a Discord interaction
            if task.interaction:
                try:
                    error_msg = "Sorry, an error occurred while processing your request. Please try again."
                    if not task.interaction.response.is_done():
                        await task.interaction.response.send_message(error_msg, ephemeral=True)
                    else:
                        await task.interaction.followup.send(error_msg, ephemeral=True)
                except Exception as inner_e:
                    logger.error(f"Failed to send error message for task {task.task_id}: {inner_e}")
        
        finally:
            # Move task to completed and clean up
            if task.task_id in self.active_tasks:
                del self.active_tasks[task.task_id]
            
            self.completed_tasks[task.task_id] = task
            
            # Limit completed task history
            if len(self.completed_tasks) > self.max_completed_history:
                oldest_task_id = min(self.completed_tasks.keys(), 
                                   key=lambda x: self.completed_tasks[x].created_at)
                del self.completed_tasks[oldest_task_id]
    
    def get_queue_status(self) -> Dict[str, Any]:
        """Get current queue status"""
        return {
            "queue_size": self.queue.qsize(),
            "active_tasks": len(self.active_tasks),
            "completed_tasks": len(self.completed_tasks),
            "is_processing": self.is_processing,
            "worker_running": self._worker_task is not None and not self._worker_task.done()
        }
    
    def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific task"""
        task = self.active_tasks.get(task_id) or self.completed_tasks.get(task_id)
        if not task:
            return None
        
        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "created_at": task.created_at.isoformat(),
            "error": task.error,
            "has_result": task.result is not None
        }

# Global task queue instance
_task_queue: Optional[TaskQueue] = None

def get_task_queue() -> TaskQueue:
    """Get the global task queue instance"""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue

def queued_task(func: Callable) -> Callable:
    """Decorator to automatically queue Discord command handlers"""
    async def wrapper(*args, **kwargs):
        queue = get_task_queue()
        task_id = await queue.enqueue_task(func, *args, **kwargs)
        logger.info(f"Command queued with task ID: {task_id}")
        return task_id
    
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper 