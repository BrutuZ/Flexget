import os
import shutil
import time
from pathlib import Path

from loguru import logger

from flexget import plugin
from flexget.config_schema import one_or_more
from flexget.event import event
from flexget.utils.pathscrub import pathscrub
from flexget.utils.template import RenderError


def get_directory_size(directory):
    """Return size in bytes (recursively).

    :param directory: Path
    """
    dir_size = 0
    for path, _, files in os.walk(directory):
        for file in files:
            filename = os.path.join(path, file)
            dir_size += os.path.getsize(filename)
    return dir_size


def get_siblings(ext, main_file_no_ext, main_file_ext, abs_path):
    siblings = {}
    files = os.listdir(abs_path)

    for filename in files:
        # skip the main file
        if filename == main_file_no_ext + main_file_ext:
            continue
        filename_lower = filename.lower()
        if not filename_lower.startswith(main_file_no_ext.lower()) or not filename_lower.endswith(
            ext.lower()
        ):
            continue
        # we have to use the length of the main file (no ext) to extract the rest of the filename
        # for the future renaming
        file_ext = filename[len(main_file_no_ext) :]
        file_path = os.path.join(abs_path, filename)
        if os.path.exists(file_path):
            siblings[file_path] = file_ext
    return siblings


class BaseFileOps:
    # Defined by subclasses
    logger = None
    along = {
        'type': 'object',
        'properties': {
            'extensions': one_or_more({'type': 'string'}),
            'subdirs': one_or_more({'type': 'string'}),
        },
        'additionalProperties': False,
        'required': ['extensions'],
    }

    def prepare_config(self, config):
        if config is True:
            return {}
        if config is False:
            return None

        if 'along' not in config:
            return config

        extensions = config['along'].get('extensions')
        subdirs = config['along'].get('subdirs')

        if extensions and not isinstance(extensions, list):
            config['along']['extensions'] = [extensions]
        if subdirs and not isinstance(subdirs, list):
            config['along']['subdirs'] = [subdirs]

        return config

    def on_task_output(self, task, config):
        config = self.prepare_config(config)
        if config is None:
            return
        for entry in task.accepted:
            if 'location' not in entry:
                self.logger.verbose(
                    'Cannot handle {} because it does not have the field location.', entry['title']
                )
                continue
            src = entry['location']
            src_isdir = src.is_dir()
            try:
                # check location
                if not src.exists():
                    self.logger.warning('location `{}` does not exists (anymore).', src)
                    continue
                if src_isdir:
                    if not config.get('allow_dir'):
                        self.logger.warning('location `{}` is a directory.', src)
                        continue
                elif not src.is_file():
                    self.logger.warning('location `{}` is not a file.', src)
                    continue
                # search for namesakes
                siblings = {}  # dict of (path=ext) pairs
                if not src_isdir and 'along' in config:
                    parent = src.parent
                    filename_no_ext = src.stem
                    filename_ext = src.suffix
                    for ext in config['along']['extensions']:
                        siblings.update(get_siblings(ext, filename_no_ext, filename_ext, parent))

                    files = os.listdir(parent)
                    files_lower = list(map(str.lower, files))
                    for subdir in config['along'].get('subdirs', []):
                        try:
                            idx = files_lower.index(subdir)
                        except ValueError:
                            continue
                        subdir_path = os.path.join(parent, files[idx])
                        if not os.path.isdir(subdir_path):
                            continue
                        for ext in config['along']['extensions']:
                            siblings.update(
                                get_siblings(ext, filename_no_ext, filename_ext, subdir_path)
                            )
                # execute action in subclasses
                self.handle_entry(task, config, entry, siblings)
            except OSError as err:
                entry.fail(str(err))
                continue

    def clean_source(self, task, config, entry):
        min_size = entry.get('clean_source', config.get('clean_source', -1))
        if min_size < 0:
            return
        base_path = entry.get('old_location', entry['location']).parent
        # everything here happens after a successful execution of the main action: the entry has been moved in a
        # different location, or it does not exists anymore. so from here we can just log warnings and move on.
        if not base_path.is_dir():
            self.logger.warning(
                'Cannot delete path `{}` because it does not exists (anymore).', base_path
            )
            return
        dir_size = get_directory_size(base_path) / 1024 / 1024
        if dir_size >= min_size:
            self.logger.info(
                'Path `{}` left because it exceeds safety value set in clean_source option.',
                base_path,
            )
            return
        if task.options.test:
            self.logger.info('Would delete `{}` and everything under it.', base_path)
            return
        try:
            shutil.rmtree(base_path)
            self.logger.info(
                'Path `{}` has been deleted because was less than clean_source safe value.',
                base_path,
            )
        except Exception as err:
            self.logger.warning('Unable to delete path `{}`: {}', base_path, err)

    def handle_entry(self, task, config, entry, siblings):
        raise NotImplementedError


class DeleteFiles(BaseFileOps):
    """Delete all accepted files."""

    schema = {
        'oneOf': [
            {'type': 'boolean'},
            {
                'type': 'object',
                'properties': {
                    'allow_dir': {'type': 'boolean'},
                    'along': BaseFileOps.along,
                    'clean_source': {'type': 'number'},
                },
                'additionalProperties': False,
            },
        ]
    }

    logger = logger.bind(name='delete')

    def handle_entry(self, task, config, entry, siblings):
        src = entry['location']
        src_isdir = os.path.isdir(src)
        if task.options.test:
            if src_isdir:
                self.logger.info('Would delete `{}` and all its content.', src)
            else:
                self.logger.info('Would delete `{}`', src)
                for s in siblings:
                    self.logger.info('Would also delete `{}`', s)
            return
        # IO errors will have the entry mark failed in the base class
        if src_isdir:
            shutil.rmtree(src)
            self.logger.info('`{}` and all its content has been deleted.', src)
        else:
            os.remove(src)
            self.logger.info('`{}` has been deleted.', src)
        # further errors will not have any effect (the entry does not exists anymore)
        for s in siblings:
            try:
                os.remove(s)
                self.logger.info('`{}` has been deleted as well.', s)
            except Exception as err:
                self.logger.warning(str(err))
        if not src_isdir:
            self.clean_source(task, config, entry)


class TransformingOps(BaseFileOps):
    # Defined by subclasses
    move = None
    destination_field = None

    def copy_permissive(self, src, dst, follow_symlinks=True):
        # this function is like shutil.copy but in case copy mode fails we ignore the error
        if os.path.isdir(dst):
            dst = os.path.join(dst, os.path.basename(src))
        shutil.copyfile(src, dst, follow_symlinks=follow_symlinks)

        try:
            shutil.copymode(src, dst, follow_symlinks=follow_symlinks)
        except OSError as err:
            if err.errno == 1:
                self.logger.warning(str(err))
            else:
                raise

    def handle_entry(self, task, config, entry, siblings):
        src = entry['location']
        src_isdir = src.is_dir()
        src_path = src.parent
        src_name = src.name

        # get the proper path and name in order of: entry, config, above split
        dst_path = entry.get(self.destination_field, config.get('to', str(src_path)))
        if config.get('rename'):
            dst_name = config['rename']
        elif entry.get('filename') and entry['filename'] != src_name:
            # entry specifies different filename than what was split from the path
            # since some inputs fill in filename it must be different in order to be used
            dst_name = entry['filename']
        else:
            dst_name = src_name

        try:
            dst_path = Path(entry.render(dst_path))
        except RenderError as err:
            entry.fail(f'Path value replacement `{dst_path}` failed: {err.args[0]}')
            return
        try:
            dst_name = entry.render(dst_name)
        except RenderError as err:
            entry.fail(f'Filename value replacement `{dst_name}` failed: {err.args[0]}')
            return

        # Clean invalid characters with pathscrub plugin
        dst_path = dst_path.expanduser()
        dst_name = pathscrub(dst_name, filename=True)

        # Join path and filename
        dst = dst_path / dst_name
        if dst == entry['location']:
            raise plugin.PluginWarning('source and destination are the same.')

        if not dst_path.exists():
            if task.options.test:
                self.logger.info('Would create `{}`', dst_path)
            else:
                self.logger.info('Creating destination directory `{}`', dst_path)
                dst_path.mkdir(parents=True)
        if not dst_path.is_dir() and not task.options.test:
            raise plugin.PluginWarning(f'destination `{dst_path}` is not a directory.')

        # unpack_safety
        if config.get('unpack_safety', entry.get('unpack_safety', True)):
            count = 0
            while True:
                if count > 60 * 30:
                    raise plugin.PluginWarning(
                        'The task has been waiting unpacking for 30 minutes'
                    )
                size = src.stat().st_size
                time.sleep(1)
                new_size = src.stat().st_size
                if size != new_size:
                    if not count % 10:
                        self.logger.verbose(
                            'File `{}` is possibly being unpacked, waiting ...', src_name
                        )
                else:
                    break
                count += 1

        src_ext = src.suffix
        dst_file, dst_ext = os.path.splitext(dst)

        # Check dst contains src_ext
        if (
            config.get('keep_extension', entry.get('keep_extension', True))
            and not src_isdir
            and dst_ext != src_ext
        ):
            self.logger.verbose('Adding extension `{}` to dst `{}`', src_ext, dst)
            dst = Path(f'{dst}{src_ext}')
            dst_file += dst_ext  # this is used for sibling files. dst_ext turns out not to be an extension!

        funct_name = 'move' if self.move else 'copy'
        funct_done = 'moved' if self.move else 'copied'

        if task.options.test:
            self.logger.info('Would {} `{}` to `{}`', funct_name, src, dst)
            for s, ext in siblings.items():
                # we cannot rely on splitext for extensions here (subtitles may have the language code)
                d = dst_file + ext
                self.logger.info('Would also {} `{}` to `{}`', funct_name, s, d)
        else:
            # IO errors will have the entry mark failed in the base class
            if self.move:
                shutil.move(src, dst)
            elif src_isdir:
                shutil.copytree(src, dst, copy_function=self.copy_permissive)
            else:
                self.copy_permissive(src, dst)
            self.logger.info('`{}` has been {} to `{}`', src, funct_done, dst)
            # further errors will not have any effect (the entry has been successfully moved or copied out)
            for s, ext in siblings.items():
                # we cannot rely on splitext for extensions here (subtitles may have the language code)
                d = dst_file + ext
                try:
                    if self.move:
                        shutil.move(s, d)
                    else:
                        shutil.copy(s, d)
                    self.logger.info('`{}` has been {} to `{}` as well.', s, funct_done, d)
                except Exception as err:
                    self.logger.warning(str(err))
        entry['old_location'] = entry['location']
        entry['location'] = dst
        if self.move and not src_isdir:
            self.clean_source(task, config, entry)


class CopyFiles(TransformingOps):
    """Copy all accepted files."""

    schema = {
        'oneOf': [
            {'type': 'boolean'},
            {
                'type': 'object',
                'properties': {
                    'to': {'type': 'string', 'format': 'path'},
                    'rename': {'type': 'string'},
                    'allow_dir': {'type': 'boolean'},
                    'unpack_safety': {'type': 'boolean'},
                    'keep_extension': {'type': 'boolean'},
                    'along': TransformingOps.along,
                },
                'additionalProperties': False,
            },
        ]
    }

    move = False
    destination_field = 'copy_to'
    logger = logger.bind(name='copy')


class MoveFiles(TransformingOps):
    """Move all accepted files."""

    schema = {
        'oneOf': [
            {'type': 'boolean'},
            {
                'type': 'object',
                'properties': {
                    'to': {'type': 'string', 'format': 'path'},
                    'rename': {'type': 'string'},
                    'allow_dir': {'type': 'boolean'},
                    'unpack_safety': {'type': 'boolean'},
                    'keep_extension': {'type': 'boolean'},
                    'along': TransformingOps.along,
                    'clean_source': {'type': 'number'},
                },
                'additionalProperties': False,
            },
        ]
    }

    move = True
    destination_field = 'move_to'
    logger = logger.bind(name='move')


@event('plugin.register')
def register_plugin():
    plugin.register(DeleteFiles, 'delete', api_ver=2)
    plugin.register(CopyFiles, 'copy', api_ver=2)
    plugin.register(MoveFiles, 'move', api_ver=2)
