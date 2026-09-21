import os
import openai

from pathlib import Path
from typing import Callable

from yaspin import yaspin


class GPTClient():
    """
    Wrapper for GPT llm interaction.
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
    ):
        """
        Initialize the GPT client.

        Args:
            model: Model to use for generation
        """
        self.model = model
        self.env_var = "OPENAI_API_KEY"
        self.api_key = os.getenv(self.env_var)
        self.client = openai.OpenAI(api_key=self.api_key)

        # Provider-specific session artifact
        self.chat_session: str

    def _spinner_operation(self, name: str, func: Callable):
        print()  # Some whitespace
        with yaspin(text=name, color="yellow") as spinner:
            result = func()
            spinner.ok("✔")
            return result

    def start_chat(self):
        conversation = self.client.conversations.create()
        self.chat_session = conversation.id

    def reset_chat(self):
        self.start_chat()

    def _send_prompt(self, chat: str, message: str, file_id: str | None):
        """
        Send prompt to a given chat.
        Optionally attach a file.
        """

        content = [{"type": "input_text", "text": message}]

        if file_id:
            content.append({
                "type": "input_file",
                "file_id": file_id
            })

        return self.client.responses.create(
            model=self.model,
            conversation=chat,
            input=[{
                "role": "user",
                "content": content
            }]  # type: ignore
        )

    def upload_file(self, file: Path) -> str:
        filename = file.name
        filetype = file.suffix

        with open(file, "rb") as f:
            llm_file = self.client.files.create(
                file=(filename, f, filetype),
                purpose="user_data"
            )
            return llm_file.id

    def send_prompt(self, prompt: str, file_id: str | None = None, verbose=True):
        """
        Send a prompt in the current chat session.
        Args:
            prompt: Text prompt for the LLM

        Returns:
            Response object from GPT
        """
        def operation():
            return self._send_prompt(self.chat_session, prompt, file_id)

        if self.chat_session is None:
            self.start_chat()

        response = self._spinner_operation(
            "Sending prompt",
            operation,
        )

        return response

    def send_prompt_with_file(self, file: Path, prompt: str, verbose=True):
        """
        Upload a file with a prompt.

        Args:
            file: LLMFileSource object containing file content
            prompt: Analysis prompt for the LLM

        Returns:
            Response object from GPT
        """
        file_id = self.upload_file(file)
        return self.send_prompt(prompt, file_id, verbose)

    def response_to_text(self, response):
        """Extract text output from a response object."""
        if response:
            return response.output_text
        return None
