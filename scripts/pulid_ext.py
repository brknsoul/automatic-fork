import io
import os
import contextlib
import gradio as gr
import numpy as np
from PIL import Image
from modules import shared, devices, errors, scripts, processing, processing_helpers, sd_models


debug = os.environ.get('SD_PULID_DEBUG', None) is not None


class Script(scripts.Script):
    def __init__(self):
        self.images = []
        self.pulid = None
        self.cache = None
        super().__init__()
        self.register() # pulid is script with processing override so xyz doesnt execute

    def title(self):
        return 'PuLID'

    def show(self, _is_img2img):
        return shared.native

    def dependencies(self):
        from installer import install, installed
        if not installed('insightface', reload=False, quiet=True):
            install('insightface', 'insightface', ignore=False)
            install('albumentations==1.4.3', 'albumentations', ignore=False, reinstall=True)
            install('pydantic==1.10.15', 'pydantic', ignore=False, reinstall=True)
        # if not installed('apex', reload=False, quiet=True):
        #     install('apex', 'apex', ignore=False)

    def register(self): # register xyz grid elements
        def apply_field(field):
            def fun(p, x, xs): # pylint: disable=unused-argument
                setattr(p, field, x)
                self.run(p)
            return fun

        import sys
        xyz_classes = [v for k, v in sys.modules.items() if 'xyz_grid_classes' in k][0]
        xyz_classes.axis_options.append(xyz_classes.AxisOption("[PuLID] Strength", float, apply_field("pulid_strength")))
        xyz_classes.axis_options.append(xyz_classes.AxisOption("[PuLID] Zero", int, apply_field("pulid_zero")))
        xyz_classes.axis_options.append(xyz_classes.AxisOption("[PuLID] Ortho", str, apply_field("pulid_ortho"), choices=lambda: ['off', 'v1', 'v2']))

    def load_images(self, files):
        self.images = []
        for file in files or []:
            try:
                if isinstance(file, str):
                    from modules.api.api import decode_base64_to_image
                    image = decode_base64_to_image(file)
                elif isinstance(file, Image.Image):
                    image = file
                elif isinstance(file, dict) and 'name' in file:
                    image = Image.open(file['name']) # _TemporaryFileWrapper from gr.Files
                elif hasattr(file, 'name'):
                    image = Image.open(file.name) # _TemporaryFileWrapper from gr.Files
                else:
                    raise ValueError(f'IP adapter unknown input: {file}')
                self.images.append(image)
            except Exception as e:
                shared.log.warning(f'IP adapter failed to load image: {e}')
        return gr.update(value=self.images, visible=len(self.images) > 0)

    # return signature is array of gradio components
    def ui(self, _is_img2img):
        with gr.Row():
            gr.HTML('<a href="https://github.com/ToTheBeginning/PuLID">&nbsp PuLID: Pure and Lightning ID Customization</a><br>')
        with gr.Row():
            strength = gr.Slider(label = 'Strength', value = 0.8, mininimum = 0, maximum = 1, step = 0.01)
            zero = gr.Slider(label = 'Zero', value = 20, mininimum = 0, maximum = 80, step = 1)
        with gr.Row():
            sampler = gr.Dropdown(label="Sampler", choices=['dpmpp_sde', 'dpmpp_2m'], value='dpmpp_sde', visible=True)
            ortho = gr.Dropdown(label="Ortho", choices=['off', 'v1', 'v2'], value='v2', visible=True)
        with gr.Row():
            files = gr.File(label='Input images', file_count='multiple', file_types=['image'], type='file', interactive=True, height=100)
        with gr.Row():
            gallery = gr.Gallery(show_label=False, value=[], visible=False, container=False, rows=1)
        files.change(fn=self.load_images, inputs=[files], outputs=[gallery])
        return [strength, zero, sampler, ortho, gallery]

    def run(self, p: processing.StableDiffusionProcessing, strength: float = 0.8, zero: int = 20, sampler: str = 'dpmpp_sde', ortho: str = 'v2', gallery: list = []): # pylint: disable=arguments-differ
        images = []
        try:
            if len(gallery) == 0:
                from modules.api.api import decode_base64_to_image
                images = getattr(p, 'pulid_images', self.images)
                images = [decode_base64_to_image(image) if isinstance(image, str) else image for image in images]
            else:
                images = [Image.open(f['name']) if isinstance(f, dict) else f for f in gallery]
            images = [np.array(image) for image in images]
        except Exception as e:
            shared.log.error(f'PuLID: failed to load images: {e}')
            return None
        if len(images) == 0:
            shared.log.error('PuLID: no images')
            return None
        supported_model_list = ['sdxl']
        if shared.sd_model_type not in supported_model_list:
            shared.log.error(f'PuLID: class={shared.sd_model.__class__.__name__} model={shared.sd_model_type} required={supported_model_list}')
            return None
        if self.pulid is None:
            self.dependencies()
            try:
                from modules import pulid # pylint: disable=redefined-outer-name
                self.pulid = pulid
                # from diffusers import pipelines
                # pipelines.auto_pipeline.AUTO_TEXT2IMAGE_PIPELINES_MAPPING["pilid"] = pulid.StableDiffusionXLPuLIDPipeline
                # pipelines.auto_pipeline.AUTO_IMAGE2IMAGE_PIPELINES_MAPPING["omnigen"] = pulid.StableDiffusionXLPuLIDPipelineImg2Img
            except Exception as e:
                shared.log.error(f'PuLID: failed to import library: {e}')
                return None
            if self.pulid is None:
                shared.log.error('PuLID: failed to load PuLID library')
                return None
        if p.batch_size > 1:
            shared.log.warning('PuLID: batch size not supported')
            p.batch_size = 1

        strength = getattr(p, 'pulid_strength', strength)
        zero = getattr(p, 'pulid_zero', zero)
        ortho = getattr(p, 'pulid_ortho', ortho)

        if shared.sd_model_type == 'sdxl' and not hasattr(shared.sd_model, 'pipe'):
            try:
                stdout = io.StringIO()
                ctx = contextlib.nullcontext if debug else contextlib.redirect_stdout(stdout)
                with ctx:
                    shared.sd_model = self.pulid.StableDiffusionXLPuLIDPipeline(
                        pipe =shared.sd_model,
                        device=devices.device,
                        sampler=sampler,
                        cache_dir=shared.opts.hfcache_dir,
                    )
                shared.sd_model.no_recurse = True
                sd_models.copy_diffuser_options(shared.sd_model, shared.sd_model.pipe)
                sd_models.move_model(shared.sd_model, devices.device) # move pipeline to device
                sd_models.set_diffuser_options(shared.sd_model, vae=None, op='model')
                devices.torch_gc()
            except Exception as e:
                shared.log.error(f'PuLID: failed to create pipeline: {e}')
                errors.display(e, 'PuLID')
                return None

        shared.log.info(f'PuLID: class={shared.sd_model.__class__.__name__} strength={strength} zero={zero} ortho={ortho} sampler={sampler} images={[i.shape for i in images]}')
        self.pulid.attention.NUM_ZERO = zero
        self.pulid.attention.ORTHO = ortho == 'v1'
        self.pulid.attention.ORTHO_v2 = ortho == 'v2'
        images = [self.pulid.resize(image, 1024) for image in images]
        shared.sd_model.debug_img_list = []
        uncond_id_embedding, id_embedding = shared.sd_model.get_id_embedding(images)

        if debug: # run pipeline directly
            shared.state.begin('PuLID')
            processing.fix_seed(p)
            p.seed = processing_helpers.get_fixed_seed(p.seed)
            p.prompt = shared.prompt_styles.apply_styles_to_prompt(p.prompt, p.styles)
            p.negative_prompt = shared.prompt_styles.apply_negative_styles_to_prompt(p.negative_prompt, p.styles)
            with devices.inference_context():
                output = shared.sd_model(
                    prompt=p.prompt,
                    negative_prompt=p.negative_prompt,
                    width=p.width,
                    height=p.height,
                    seed=p.seed,
                    num_inference_steps=p.steps,
                    guidance_scale=p.cfg_scale,
                    id_embedding=id_embedding,
                    uncond_id_embedding=uncond_id_embedding,
                    id_scale=strength,
                    )[0]
            info = processing.create_infotext(p)
            processed = processing.Processed(p, [output], info=info)
            shared.state.end('PuLID')
        else: # let processing run the pipeline
            p.task_args['id_embedding'] = id_embedding
            p.task_args['uncond_id_embedding'] = uncond_id_embedding
            p.task_args['id_scale'] = strength
            if len(getattr(p, 'init_images', [])) > 0:
                p.task_args['image'] = p.init_images[0]
                p.task_args['strength'] = p.denoising_strength
            p.extra_generation_params["PuLID"] = f'Strength={strength} Zero={zero} Ortho={ortho}'
            if getattr(p, 'xyz', False): # xyz will run its own processing
                return None
            processed: processing.Processed = processing.process_images(p) # runs processing using main loop

        # interim = [Image.fromarray(img) for img in shared.sd_model.debug_img_list]
        # shared.log.debug(f'PuLID: time={t1-t0:.2f}')
        return processed

    def after(self, p: processing.StableDiffusionProcessing, processed: processing.Processed, *args): # pylint: disable=unused-argument
        if hasattr(shared.sd_model, 'pipe') and shared.sd_model_type == "sdxl":
            if hasattr(shared.sd_model, 'app'):
                shared.sd_model.app = None
                shared.sd_model.ip_adapter = None
                shared.sd_model.face_helper = None
                shared.sd_model.clip_vision_model = None
                shared.sd_model.handler_ante = None
                devices.torch_gc(force=True)
            shared.sd_model = shared.sd_model.pipe
            # shared.log.debug(f'PuLID restore: class={shared.sd_model.__class__.__name__}')
        return processed
