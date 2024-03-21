import os
import sys
import shutil
from os.path import join, exists
from omegaconf import DictConfig, OmegaConf
from pyprojroot import here
from utils import tools

sys.path.append(str(here()))
from simpet import SimPET


class BrainVISET(object):

    """
    This class provides iterative viset for brain images.
    You have to initialize the class with a params file.
    The inputs to this class are a PET and a CT from the same patient.
    This class will generate initial maps and call iteratively the simulation class.
    The number of iterations is set in the main config file.
    Before using SimPET, check out the README.

    """

    def __init__(self, cfg: DictConfig):
        # Initialization
        self.simpet_dir = here()
        self.cfg_omega = cfg
        self.cfg = OmegaConf.to_container(cfg)
        self.config = {k: v for k, v in self.cfg.items() if k != "params"}
        self.params = self.cfg["params"]
        self.scanner = self.params["scanner"]
        self.sim_type = self.params.get("sim_type")

        # This will load the scanner params for the selected scanner
        self.scanner_model = self.params.get("scanner")

        spm_path = self.config.get("spm_path")
        matlab_path = self.config.get("matlab_mcr_path")
        self.spmrun = "sh %s/run_spm12.sh %s batch" % (spm_path, matlab_path)

        self.dir_data = self.config.get("dir_data_path")
        if not self.dir_data:
            self.dir_data = join(self.simpet_dir, "Data")

        self.dir_results = self.config.get("dir_results_path")
        if not self.dir_results:
            self.dir_results = join(self.simpet_dir, "Results")

        if not exists(self.dir_results):
            os.makedirs(self.dir_results)

    def run(self):
        print("Welcome to brainviset")

        patient_dir = join(self.dir_data, self.params.get("patient_dirname"))
        pet = join(patient_dir, self.params.get("pet_image"))
        if self.params.get("ct_image"):
            ct = join(patient_dir, self.params.get("ct_image"))
        else:
            ct = ""
        mri = join(patient_dir, self.params.get("mri_image"))

        output_name = self.params.get("output_dir")
        output_dir = join(self.dir_results, output_name)
        maps_dir = join(output_dir, "Maps")
        log_file = join(output_dir, "log_sim.log")

        number_of_its = self.params.get("maximumIteration")
        axialFOV = self.scanner.get("axial_fov")

        if exists(output_dir):
            if self.config.get("interactive_mode") == 1:
                print(
                    "The introduced output dir already has a brainviset simulation.Proceeding will delete it."
                )
                remove = input(" Write 'Y' to delete it: ")
                print(
                    "You can disable this prompt by deactivating interactive mode in the config file."
                )
                if remove == "Y":
                    shutil.rmtree(output_dir)
                else:
                    raise Exception("The simulation was aborted.")
                    ## Place some logging here
                    sys.exit(1)
            else:
                shutil.rmtree(output_dir)

        os.makedirs(output_dir)
        os.makedirs(maps_dir)

        # We will start generating the initial maps from the PET and the MRI
        msg = "Generating initial act and att maps from PET, (CT), and MRI data..."
        print(msg)
        act_map, att_map = tools.petmr2maps(
            pet, mri, ct, log_file, self.spmrun, maps_dir
        )

        self.params["att_map"] = att_map

        max_num_it = int(number_of_its)
        if max_num_it >= 1:
            it = 0
            old_corrCoef = 0.0
            new_corrCoef = 0.0
            more_its = True
            while (it < max_num_it) & more_its:
                log_file_its = join(output_dir, "log_sim_It_%s.log" % str(it))
                output_dir_aux = join(output_dir, "It_%s" % str(it))
                components = os.path.split(pet)
                preproc_pet = os.path.join(
                    components[0], "r" + components[1][0:-3] + "hdr"
                )

                self.params["act_map"] = act_map
                self.params["output_dir"] = output_dir_aux

                msg = "Simulating brain image for iteration %s of %s" % (
                    str(it),
                    number_of_its,
                )
                print(msg)
                tools.log_message(log_file_its, msg)
                it_sim = SimPET(self.cfg_omega)
                it_sim.simset_simulation(act_map, att_map, output_dir_aux)

                recons_algorithm = self.scanner.get("recons_type")
                recons_it = self.scanner.get("numberOfIterations")

                rec_file = join(
                    output_dir_aux,
                    "SimSET_Sim_" + self.params.get("scanner"),
                    recons_algorithm,
                    "rec_%s_%s.hdr" % (recons_algorithm, recons_it),
                )

                if exists(rec_file):
                    print("Updating activity map")
                    tools.log_message(log_file_its, "Updating activity maps")
                    rrec_file = join(
                        output_dir_aux,
                        "SimSET_Sim_" + self.params.get("scanner"),
                        recons_algorithm,
                        "rrec_%s_%s.hdr" % (recons_algorithm, recons_it),
                    )
                    new_corrCoef = tools.compute_corr_coeff(
                        preproc_pet, rrec_file, log_file_its
                    )
                    msg = "Correlation coefficient between images is %s " % (
                        new_corrCoef
                    )
                    print(msg)
                    tools.log_message(log_file_its, msg)
                    if new_corrCoef > 0.99:
                        msg = (
                            "No further iterations are necessary. Final activity map is %s"
                            % (act_map)
                        )
                        more_its = False
                    elif old_corrCoef > new_corrCoef:
                        fin_act_map = join(
                            maps_dir, act_map[0:-5] + "%s.hdr" % str(it - 1)
                        )
                        msg = (
                            "No further iterations will be done.  The correlation coefficient has worsened. Final activity map is %s"
                            % (fin_act_map)
                        )
                        more_its = False
                        # remove all the folders relatively to the last iteration done?
                    else:
                        it = it + 1
                        msg = "Not converging yet. Preparing for iteration %s of %s" % (
                            it,
                            number_of_its,
                        )
                        old_corrCoef = new_corrCoef
                        updated_act_map = join(
                            maps_dir, act_map[0:-5] + "%s.hdr" % str(it)
                        )
                        tools.update_act_map(
                            self.spmrun,
                            act_map,
                            att_map,
                            preproc_pet,
                            rec_file,
                            updated_act_map,
                            axialFOV,
                            log_file_its,
                        )
                        act_map = updated_act_map

                    print(msg)
                    tools.log_message(log_file, msg)
                else:
                    raise Exception("The brainviset process was aborted.")
                    ## Place some logging here
                    sys.exit(1)

            if more_its:
                msg = (
                    "Maximum number of iterations reached. Final activity map is %s"
                    % (updated_act_map)
                )
                print(msg)
                tools.log_message(log_file, msg)
