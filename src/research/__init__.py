"""Research domain: polymer and plastics works from OpenAlex.

Pure functions plan each load (which partitions, which SQL); the Lambda
handlers in `handlers` only add I/O. Everything here uses the standard library
and boto3, which the Lambda runtime provides, so nothing needs bundling.
"""
