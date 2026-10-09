
# 1. Choose a Python environment
FROM python:3.12-slim

# 2. Set the working directory
WORKDIR /app

# 3. Copy the dependency list
COPY requirements.txt .

# 4. Install the required Python libraries
RUN pip install --no-cache-dir -r requirements.txt

# 5. Create a non-root user
RUN useradd --create-home appuser

# 6. Copy application files
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser migrations/ ./migrations/

# 7. Run the app as a regular user
USER appuser

# 8. Document the application's port
EXPOSE 8080

# 9. Start the FastAPI application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]