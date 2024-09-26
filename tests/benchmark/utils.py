"""Utility functions for the benchmarking."""

import json
import os
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

import git
import pandas as pd

from sdv.io.local import CSVHandler
from tests._external.gdrive_utils import get_latest_file, read_excel, save_to_gdrive
from tests._external.slack_utils import post_slack_message

GDRIVE_OUTPUT_FOLDER = '16SkTOyQ3xkJDPJbyZCusb168JwreW5bm'
PYTHON_VERSION = f'{sys.version_info.major}.{sys.version_info.minor}'
TEMPRESULTS = Path(f'results/{sys.version_info.major}.{sys.version_info.minor}.json')


def get_previous_dtype_result(dtype, sdtype, method, python_version=PYTHON_VERSION):
    """Return previous result for a given ``dtype`` and method."""
    data = get_previous_results()
    df = data[python_version]
    try:
        filtered_row = df[(df['dtype'] == dtype) & (df['sdtype'] == sdtype)]
        value = filtered_row[method].to_numpy()[0]
        previously_seen = True
    except (IndexError, KeyError):
        value = False
        previously_seen = False

    return value, previously_seen


@lru_cache()
def get_previous_results():
    """Get the last run for the dtype benchmarking."""
    latest_file = get_latest_file(GDRIVE_OUTPUT_FOLDER)
    df = read_excel(latest_file['id'])
    return df


def _load_temp_results(filename):
    df = pd.read_json(filename)
    df.iloc[:, 2:] = df.groupby(['dtype', 'sdtype']).transform(lambda x: x.ffill().bfill())
    for column in df.columns:
        if column not in ('sdtype', 'dtype'):
            df[column] = df[column].astype(bool)

    return df.drop_duplicates().reset_index(drop=True)


def _get_output_filename():
    repo = git.Repo(search_parent_directories=True)
    commit_id = repo.head.object.hexsha
    today = str(date.today())
    output_filename = f'{today}-{commit_id}'
    return output_filename


def compare_previous_result_with_current():
    """Compare the previous result with the current and post a message on slack."""
    for result in Path('results/').rglob('*.json'):
        python_version = result.stem
        current_results = _load_temp_results(result)
        csv_output = Path(f'results/{python_version}.csv')
        current_results.to_csv(csv_output, index=False)

        new_supported_dtypes = []
        unsupported_dtypes = []
        previously_unseen_dtypes = []

        for index, row in current_results.iterrows():
            dtype = row['dtype']
            sdtype = row['sdtype']
            for col in current_results.columns[1:]:
                current_value = row[col]
                stored_value, previously_seen = get_previous_dtype_result(
                    dtype,
                    sdtype,
                    col,
                    python_version,
                )

                if current_value and not stored_value:
                    new_supported_dtypes.append({
                        'dtype': dtype,
                        'sdtype': sdtype,
                        'method': col,
                        'python_version': python_version,
                    })

                elif not current_value and stored_value:
                    unsupported_dtypes.append({
                        'dtype': dtype,
                        'sdtype': sdtype,
                        'method': col,
                        'python_version': python_version,
                    })

                if not previously_seen:
                    previously_unseen_dtypes.append({
                        'dtype': dtype,
                        'sdtype': sdtype,
                        'method': col,
                        'python_version': python_version,
                    })

    return {
        'unsupported_dtypes': pd.DataFrame(unsupported_dtypes),
        'new_supported_dtypes': pd.DataFrame(new_supported_dtypes),
        'previously_unseen_dtypes': pd.DataFrame(previously_unseen_dtypes),
    }


def save_results_to_json(results, filename=None):
    """Save results to a JSON file, categorizing by `dtype`.

    This function saves the `results` dictionary to a specified JSON file.
    The dictionary must contain a `dtype` key, which is used as a category
    to group the results in the file. If the file already exists, it loads
    the existing data, updates the `dtype` category with new values from
    `results`, and saves the updated content back to the file. If the file
    does not exist it doesn't write.

    Args:
        results (dict):
            A dictionary containing the data to save. Must include the
            key `dtype` that specifies the category under which the data
            will be stored in the JSON file.
        filename (str, optional):
            The name of the JSON file where the results will be saved.
            Defaults to `None`.
    """
    filename = filename or TEMPRESULTS

    if os.path.exists(filename):
        with open(filename, 'r') as file:
            try:
                json_data = json.load(file)
            except json.JSONDecodeError:
                json_data = []

        json_data.append(results)
        with open(filename, 'w') as file:
            json.dump(json_data, file, indent=4)


def calculate_support_percentage(df):
    """Calculate the percentage of supported features (True) for each dtype in a DataFrame."""
    feature_columns = df.drop(columns=['dtype'])
    # Calculate percentage of TRUE values for each row (dtype)
    percentage_support = feature_columns.mean(axis=1) * 100
    return pd.DataFrame({'dtype': df['dtype'], 'percentage_supported': percentage_support})


def compare_and_store_results_in_gdrive():
    csv_handler = CSVHandler()
    comparison_results = compare_previous_result_with_current()

    results = csv_handler.read('results/')
    sorted_results = {}

    slack_messages = []
    for key, value in comparison_results.items():
        if not value.empty:
            sorted_results[key] = value
            if key == 'unsupported_dtypes':
                slack_messages.append(':fire: New unsupported DTypes!')
            elif key == 'new_supported_dtypes':
                slack_messages.append(':party_blob: New DTypes supported!')

    if len(slack_messages) == 0:
        slack_messages.append(':dealwithit: No new changes to the DTypes in SDV.')

    for key, value in results.items():
        sorted_results[key] = value

    file_id = save_to_gdrive(GDRIVE_OUTPUT_FOLDER, sorted_results)

    slack_messages.append(
        f'See <https://docs.google.com/spreadsheets/d/{file_id}|dtypes summary and details>'
    )
    slack_message = '\n'.join(slack_messages)
    post_slack_message('sdv-alerts', slack_message)


if __name__ == '__main__':
    compare_and_store_results_in_gdrive()
