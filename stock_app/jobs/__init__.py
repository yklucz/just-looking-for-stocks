"""Durable operational automation, independent of Flask.

JobService.health() and status() are application services for future API consumers.
"""
from .core import JobDefinition, JobRunner
from .service import JobService

__all__ = ['JobDefinition', 'JobRunner', 'JobService']
