# eventfrog-ticketing-stats

EventFrog Ticketing stats generator using their Organizer API.

## Usage

1. Clone the repository and navigate to the project directory.
2. Install the required dependencies using `pip install -r requirements.txt`.
3. Create a `.env` file in the project root and add your EventFrog API key:
    ```
    EVENTFROG_API_KEY=your_api_key_here
    ```
   No API key yet? Create one at https://eventfrog.ch/en/account/api-keys (Scope: "Organizer API: Read")
4. Optionally add the following files:
    - `config/categories.json`: Category mappings and colors:
         ```json
         {
            "mappings": {
                "Kids (up to 16. birthday)": "Kids",
                "Kids (up to 16)": "Kids"
            },
            "colors": {
               "Kids": "#33FF57",
               "Adults": "#3357FF"
            }
         }
         ```
5. Run the script using `python main.py` to generate the ticketing stats.
6. Optional: Create a symbolic link to the output file for easier access. Example:
    ```
    ln -s ./html /path/to/webserver/stats
    ```

## API Reference

https://docs.api.eventfrog.net/#organizer

⇒ OpenAPI JSON/YAML download links are available at the top of the page.
